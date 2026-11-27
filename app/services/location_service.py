from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Dict, Iterable, List, Optional, Set, Tuple
import anyio
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import session_scope
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.network import build_qr_png, resolve_effective_base_url, validate_http_url
from app.models.audit import AuditLog
from app.models.item import Item
from app.models.location import Location, LocationPhoto
from app.repositories.location_repository import location_repository
from app.schemas.item import LocationPhotos, VisionAnalysisResponse
from app.schemas.location import (
    Breadcrumb,
    GridSlotEnsureRequest,
    GridSlotMoveRequest,
    LocationCreate,
    LocationGridSave,
    LocationPhotoRead,
    LocationRead,
    LocationTree,
    LocationUpdate,
)
from app.services.photo_service import photo_service
from app.services.vision_service import vision_service


_GRID_SLOT_LOCK = threading.RLock()
_REPARENT_LOCK = threading.Lock()


class LocationHierarchySnapshot:
    def __init__(
        self,
        locations: List[Location],
        direct_counts: Dict[int, int],
        photos_by_location: Optional[Dict[int, List[LocationPhoto]]] = None,
    ) -> None:
        self.loc_by_id: Dict[int, Location] = {loc.id: loc for loc in locations}
        self.children_by_parent: Dict[Optional[int], List[Location]] = {}
        for loc in locations:
            parent_key = loc.parent_id if (loc.parent_id and loc.parent_id in self.loc_by_id) else None
            self.children_by_parent.setdefault(parent_key, []).append(loc)

        self.direct_counts = direct_counts
        self.photos_by_location: Dict[int, List[LocationPhoto]] = photos_by_location or {}
        self._crumbs_cache: Dict[int, List[Breadcrumb]] = {}
        self._total_counts_cache: Dict[int, int] = {}
        self._photos_cache: Dict[int, LocationPhotos] = {}

    def breadcrumbs_for(self, loc_id: int) -> List[Breadcrumb]:
        if loc_id in self._crumbs_cache:
            return self._crumbs_cache[loc_id]

        crumbs: List[Breadcrumb] = []
        curr = self.loc_by_id.get(loc_id)
        visited: Set[int] = set()
        while curr and curr.id not in visited:
            visited.add(curr.id)
            crumbs.insert(0, Breadcrumb(id=curr.id, name=curr.name, label=curr.label, location_type=curr.location_type))
            curr = self.loc_by_id.get(curr.parent_id) if curr.parent_id else None

        self._crumbs_cache[loc_id] = crumbs
        return crumbs

    def full_path_for(self, loc_id: int) -> str:
        return " > ".join(c.name for c in self.breadcrumbs_for(loc_id))

    def total_items_for(self, loc_id: int, visiting: Optional[Set[int]] = None) -> int:
        if loc_id in self._total_counts_cache:
            return self._total_counts_cache[loc_id]
        if visiting is None:
            visiting = set()
        if loc_id in visiting:
            return 0
        visiting.add(loc_id)

        total = self.direct_counts.get(loc_id, 0)
        for child in self.children_by_parent.get(loc_id, []):
            total += self.total_items_for(child.id, visiting)

        self._total_counts_cache[loc_id] = total
        return total

    def inherited_photos_for(self, loc_id: int) -> LocationPhotos:
        if loc_id in self._photos_cache:
            return self._photos_cache[loc_id]

        container_photo: Optional[str] = None
        context_photo: Optional[str] = None
        curr = self.loc_by_id.get(loc_id)
        visited: Set[int] = set()
        while curr and curr.id not in visited:
            visited.add(curr.id)
            if not container_photo and curr.container_photo:
                container_photo = curr.container_photo
            if not context_photo and curr.context_photo:
                context_photo = curr.context_photo
            curr = self.loc_by_id.get(curr.parent_id) if curr.parent_id else None

        resolved = LocationPhotos(container_photo=container_photo, context_photo=context_photo)
        self._photos_cache[loc_id] = resolved
        return resolved

    def to_read(self, loc: Location) -> LocationRead:
        children = self.children_by_parent.get(loc.id, [])
        loc_photos = [LocationPhotoRead.model_validate(p) for p in self.photos_by_location.get(loc.id, [])]
        return LocationRead(
            id=loc.id,
            name=loc.name,
            label=loc.label,
            description=loc.description,
            location_type=loc.location_type,
            parent_id=loc.parent_id,
            container_photo=loc.container_photo,
            context_photo=loc.context_photo,
            photos=loc_photos,
            grid_rows=loc.grid_rows,
            grid_cols=loc.grid_cols,
            grid_config=loc.grid_config,
            grid_row=loc.grid_row,
            grid_col=loc.grid_col,
            grid_row_span=loc.grid_row_span,
            grid_col_span=loc.grid_col_span,
            full_path=self.full_path_for(loc.id),
            breadcrumbs=self.breadcrumbs_for(loc.id),
            item_count=self.direct_counts.get(loc.id, 0),
            total_item_count=self.total_items_for(loc.id),
            child_count=len(children),
            created_at=loc.created_at,
            updated_at=loc.updated_at,
        )


class LocationService:
    @staticmethod
    def build_snapshot(
        db: Session,
        include_counts: bool = True,
        include_photos: bool = True,
    ) -> LocationHierarchySnapshot:
        locations = location_repository.list_all_ordered(db)
        counts = location_repository.get_direct_item_counts(db) if include_counts else {}
        photos_by_location: Dict[int, List[LocationPhoto]] = {}
        if include_photos:
            photo_rows = (
                db.execute(
                    select(LocationPhoto).order_by(LocationPhoto.created_at.desc(), LocationPhoto.id.desc())
                )
                .scalars()
                .all()
            )
            for p in photo_rows:
                photos_by_location.setdefault(p.location_id, []).append(p)
        return LocationHierarchySnapshot(locations, counts, photos_by_location)

    @staticmethod
    def build_scoped_snapshot(
        db: Session,
        location_ids: Iterable[int],
        include_counts: bool = True,
        include_photos: bool = True,
        photos_for_descendants: bool = False,
    ) -> LocationHierarchySnapshot:
        seeds = [int(i) for i in set(location_ids) if i is not None]
        if not seeds:
            return LocationHierarchySnapshot([], {}, {})

        needed_ids = location_repository.get_ancestor_ids(db, seeds)
        if include_counts or photos_for_descendants:
            descendant_ids = location_repository.get_descendant_ids(db, seeds)
            needed_ids |= descendant_ids
        else:
            descendant_ids = set(seeds)

        locations = location_repository.list_by_ids(db, needed_ids)
        counts = location_repository.get_direct_item_counts_for(db, descendant_ids) if include_counts else {}
        photos_by_location: Dict[int, List[LocationPhoto]] = {}
        if include_photos:
            photo_target_ids = list(descendant_ids if photos_for_descendants else set(seeds))
            if photo_target_ids:
                photo_rows = (
                    db.execute(
                        select(LocationPhoto)
                        .where(LocationPhoto.location_id.in_(photo_target_ids))
                        .order_by(LocationPhoto.created_at.desc(), LocationPhoto.id.desc())
                    )
                    .scalars()
                    .all()
                )
                for p in photo_rows:
                    photos_by_location.setdefault(p.location_id, []).append(p)
        return LocationHierarchySnapshot(locations, counts, photos_by_location)

    @staticmethod
    def get_or_404(db: Session, location_id: int) -> Location:
        loc = location_repository.get_by_id(db, location_id)
        if not loc:
            raise NotFoundError("Location not found.")
        return loc

    @classmethod
    def validate_no_cycle(cls, db: Session, location_id: int, new_parent_id: Optional[int]) -> None:
        if new_parent_id is None:
            return
        if location_id == new_parent_id:
            raise BadRequestError("A location cannot be its own parent.")

        curr_id: Optional[int] = new_parent_id
        visited: Set[int] = set()
        while curr_id is not None:
            if curr_id in visited:
                break
            visited.add(curr_id)
            if curr_id == location_id:
                raise BadRequestError("Cannot set a descendant as the parent location (circular reference).")
            curr_id = db.execute(
                select(Location.parent_id).where(Location.id == curr_id)
            ).scalar_one_or_none()

    @classmethod
    def _validate_child_grid_placement(
        cls,
        parent: Optional[Location],
        grid_row: Optional[int],
        grid_col: Optional[int],
        grid_row_span: Optional[int] = 1,
        grid_col_span: Optional[int] = 1,
        exclude_id: Optional[int] = None,
    ) -> None:
        if grid_row is None and grid_col is None:
            return
        if (grid_row is None) != (grid_col is None):
            raise BadRequestError("Both grid_row and grid_col must be specified together.")
        if parent is None:
            return

        rs = max(1, grid_row_span or 1)
        cs = max(1, grid_col_span or 1)
        end_r = grid_row + rs - 1
        end_c = grid_col + cs - 1

        if parent.grid_rows and parent.grid_cols:
            if end_r > parent.grid_rows or end_c > parent.grid_cols:
                raise BadRequestError(
                    f"Slot at ({grid_row}, {grid_col}) with span ({rs}x{cs}) "
                    f"exceeds {parent.grid_rows}x{parent.grid_cols} grid."
                )

        for sib in (parent.children or []):
            if exclude_id is not None and sib.id == exclude_id:
                continue
            if sib.grid_row is None or sib.grid_col is None:
                continue
            sib_rs = max(1, sib.grid_row_span or 1)
            sib_cs = max(1, sib.grid_col_span or 1)
            sib_end_r = sib.grid_row + sib_rs - 1
            sib_end_c = sib.grid_col + sib_cs - 1
            overlaps = not (
                sib_end_r < grid_row
                or sib.grid_row > end_r
                or sib_end_c < grid_col
                or sib.grid_col > end_c
            )
            if overlaps:
                raise BadRequestError(
                    f"Grid cell ({grid_row}, {grid_col}) overlaps existing compartment '{sib.name}'."
                )

    @classmethod
    def create(cls, db: Session, data: LocationCreate) -> Location:
        if data.parent_id is not None and (data.grid_row is not None or data.grid_col is not None):
            with _GRID_SLOT_LOCK:
                return cls._create_unlocked(db, data)
        return cls._create_unlocked(db, data)

    @classmethod
    def _create_unlocked(cls, db: Session, data: LocationCreate) -> Location:
        parent: Optional[Location] = None
        if data.parent_id is not None:
            parent = location_repository.get_by_id(db, data.parent_id)
            if not parent:
                raise NotFoundError(f"Parent location {data.parent_id} does not exist.")
            cls._validate_child_grid_placement(
                parent=parent,
                grid_row=data.grid_row,
                grid_col=data.grid_col,
                grid_row_span=data.grid_row_span,
                grid_col_span=data.grid_col_span,
            )

        loc_type_val = data.location_type.value if hasattr(data.location_type, "value") else str(data.location_type)
        photo_service.register_pending_reference(db, data.container_photo)
        photo_service.register_pending_reference(db, data.context_photo)
        clean_label = (data.label or "").strip()[:40] or None
        loc = Location(
            name=data.name.strip(),
            label=clean_label,
            description=data.description,
            location_type=loc_type_val,
            parent_id=data.parent_id,
            container_photo=data.container_photo,
            context_photo=data.context_photo,
            grid_rows=data.grid_rows,
            grid_cols=data.grid_cols,
            grid_config=data.grid_config,
            grid_row=data.grid_row,
            grid_col=data.grid_col,
            grid_row_span=data.grid_row_span,
            grid_col_span=data.grid_col_span,
        )
        db.add(loc)
        db.flush()

        for field_val, ptype in ((loc.container_photo, "container"), (loc.context_photo, "context")):
            if field_val:
                stem = Path(field_val).stem
                thumb_rel = f"/uploads/thumbnails/{stem}_thumb.webp"
                db.add(
                    LocationPhoto(
                        location_id=loc.id,
                        photo_path=field_val,
                        thumbnail_path=thumb_rel if field_val.startswith("/uploads/originals/") else None,
                        photo_type=ptype,
                    )
                )
        db.flush()

        if data.auto_generate_children:
            cfg = data.auto_generate_children
            if cfg.end < cfg.start:
                raise BadRequestError("auto_generate_children end must be >= start.")
            if (cfg.end - cfg.start + 1) > 100:
                raise BadRequestError("Cannot auto-generate more than 100 compartments at once.")
            prefix = cfg.prefix.strip()
            child_type_val = cfg.child_type.value if hasattr(cfg.child_type, "value") else str(cfg.child_type)
            for num in range(cfg.start, cfg.end + 1):
                child = Location(
                    name=f"{prefix} {num}",
                    location_type=child_type_val,
                    parent_id=loc.id,
                    description=f"Compartment {prefix} {num} in {loc.name}",
                )
                db.add(child)
            db.flush()

        db.refresh(loc)
        return loc

    @classmethod
    def update(cls, db: Session, location_id: int, data: LocationUpdate) -> Location:
        grid_keys = {"parent_id", "grid_row", "grid_col", "grid_row_span", "grid_col_span", "grid_rows", "grid_cols"}
        needs_reparent = "parent_id" in data.model_fields_set
        needs_grid = bool(grid_keys & data.model_fields_set)
        if not needs_reparent and not needs_grid:
            return cls._update_unlocked(db, location_id, data)
        if needs_reparent:
            # Serialize reparenting so concurrent moves can't each pass the cycle check and form a loop.
            with _REPARENT_LOCK, _GRID_SLOT_LOCK:
                db.expire_all()
                loc = cls._update_unlocked(db, location_id, data)
                db.commit()
                return loc
        with _GRID_SLOT_LOCK:
            return cls._update_unlocked(db, location_id, data)

    @classmethod
    def _update_unlocked(cls, db: Session, location_id: int, data: LocationUpdate) -> Location:
        loc = cls.get_or_404(db, location_id)
        old_photos: Tuple[Optional[str], Optional[str]] = (loc.container_photo, loc.context_photo)

        update_dict = data.model_dump(exclude_unset=True)
        if "parent_id" in update_dict and update_dict["parent_id"] != loc.parent_id:
            new_parent_id = update_dict["parent_id"]
            if new_parent_id is not None:
                parent = location_repository.get_by_id(db, new_parent_id)
                if not parent:
                    raise NotFoundError(f"Parent location {new_parent_id} does not exist.")
            cls.validate_no_cycle(db, location_id, new_parent_id)

        grid_keys = {"parent_id", "grid_row", "grid_col", "grid_row_span", "grid_col_span"}
        if grid_keys & set(update_dict.keys()):
            eff_parent_id = update_dict.get("parent_id", loc.parent_id)
            eff_row = update_dict.get("grid_row", loc.grid_row)
            eff_col = update_dict.get("grid_col", loc.grid_col)
            eff_rs = update_dict.get("grid_row_span", loc.grid_row_span)
            eff_cs = update_dict.get("grid_col_span", loc.grid_col_span)
            eff_parent = location_repository.get_by_id(db, eff_parent_id) if eff_parent_id else None
            cls._validate_child_grid_placement(
                parent=eff_parent,
                grid_row=eff_row,
                grid_col=eff_col,
                grid_row_span=eff_rs,
                grid_col_span=eff_cs,
                exclude_id=loc.id,
            )

        if {"grid_rows", "grid_cols"} & set(update_dict.keys()):
            new_rows = update_dict.get("grid_rows", loc.grid_rows)
            new_cols = update_dict.get("grid_cols", loc.grid_cols)
            for ch in (loc.children or []):
                if ch.grid_row is None or ch.grid_col is None:
                    continue
                end_r = ch.grid_row + max(1, ch.grid_row_span or 1) - 1
                end_c = ch.grid_col + max(1, ch.grid_col_span or 1) - 1
                if (new_rows and end_r > new_rows) or (new_cols and end_c > new_cols):
                    raise BadRequestError(
                        f"Cannot shrink grid: compartment '{ch.name}' would fall outside {new_rows}x{new_cols}."
                    )

        for key, value in update_dict.items():
            if key == "name" and value is not None:
                setattr(loc, key, value.strip())
            elif key == "label":
                setattr(loc, key, (value or "").strip()[:40] or None)
            elif key == "location_type" and value is not None:
                setattr(loc, key, value.value if hasattr(value, "value") else str(value))
            else:
                setattr(loc, key, value)

        photo_cleanup_candidates: Set[str] = set()
        for field_name, ptype in (("container_photo", "container"), ("context_photo", "context")):
            if field_name not in update_dict:
                continue
            new_val = update_dict[field_name]
            photo_service.register_pending_reference(db, new_val)
            if new_val is None:
                matching_rows = list(
                    db.execute(
                        select(LocationPhoto).where(
                            LocationPhoto.location_id == loc.id,
                            LocationPhoto.photo_type == ptype,
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in matching_rows:
                    if row.photo_path:
                        photo_cleanup_candidates.add(row.photo_path)
                    db.delete(row)
            else:
                existing_row = db.execute(
                    select(LocationPhoto).where(
                        LocationPhoto.location_id == loc.id,
                        LocationPhoto.photo_path == new_val,
                    )
                ).scalar_one_or_none()
                if not existing_row:
                    stem = Path(new_val).stem
                    thumb_rel = f"/uploads/thumbnails/{stem}_thumb.webp"
                    db.add(
                        LocationPhoto(
                            location_id=loc.id,
                            photo_path=new_val,
                            thumbnail_path=thumb_rel if new_val.startswith("/uploads/originals/") else None,
                            photo_type=ptype,
                        )
                    )

        db.flush()
        db.refresh(loc)

        for old_photo in old_photos:
            if old_photo and old_photo not in (loc.container_photo, loc.context_photo):
                photo_cleanup_candidates.add(old_photo)
        for photo_url in photo_cleanup_candidates:
            photo_service.schedule_photo_cleanup(db, photo_url)

        return loc

    @classmethod
    def delete(cls, db: Session, location_id: int) -> None:
        loc = cls.get_or_404(db, location_id)
        is_space = loc.location_type in ("room", "site")

        detached_containers: List[Location] = []
        deleted_nodes: List[Location] = []

        def walk_subtree(node: Location) -> None:
            if is_space and node.id != loc.id and node.location_type == "container":
                detached_containers.append(node)
                return
            deleted_nodes.append(node)
            for ch in list(node.children or []):
                walk_subtree(ch)

        walk_subtree(loc)

        detached_container_ids = [c.id for c in detached_containers]
        deleted_node_ids = [n.id for n in deleted_nodes]

        candidate_photos: Set[str] = set()
        for dnode in deleted_nodes:
            if dnode.container_photo:
                candidate_photos.add(dnode.container_photo)
            if dnode.context_photo:
                candidate_photos.add(dnode.context_photo)
        if deleted_node_ids:
            gallery_paths = db.execute(
                select(LocationPhoto.photo_path).where(LocationPhoto.location_id.in_(deleted_node_ids))
            ).scalars()
            for gpath in gallery_paths:
                if gpath:
                    candidate_photos.add(gpath)

        from sqlalchemy import update

        # Preserve containers (boxes, labels, drawers, items) in "Needs a home"
        if detached_container_ids:
            db.execute(
                update(Location)
                .where(Location.id.in_(detached_container_ids))
                .values(parent_id=None)
            )

        # Preserve any loose items in deleted nodes in "Needs a home"
        if deleted_node_ids:
            cls._move_items_with_audit(db, deleted_node_ids, None, "Location deleted; moved to Needs a home")

        db.flush()
        db.expire_all()
        loc_to_delete = db.get(Location, location_id)
        if loc_to_delete is not None:
            db.delete(loc_to_delete)
            db.flush()

        for photo_url in candidate_photos:
            photo_service.schedule_photo_cleanup(db, photo_url)

    @classmethod
    def get_read_by_id(cls, db: Session, location_id: int) -> LocationRead:
        snapshot = cls.build_scoped_snapshot(db, [location_id], include_counts=True, include_photos=True)
        loc = snapshot.loc_by_id.get(location_id)
        if not loc:
            raise NotFoundError("Location not found.")
        return snapshot.to_read(loc)

    @classmethod
    def get_all(cls, db: Session) -> List[LocationRead]:
        snapshot = cls.build_snapshot(db, include_counts=True)
        return [snapshot.to_read(loc) for loc in snapshot.loc_by_id.values()]

    @staticmethod
    def _assemble_tree_map(snapshot: LocationHierarchySnapshot) -> Tuple[Dict[int, LocationTree], List[LocationTree]]:
        node_map: Dict[int, LocationTree] = {}
        for loc in snapshot.loc_by_id.values():
            read_model = snapshot.to_read(loc)
            node_map[loc.id] = LocationTree(**read_model.model_dump(), children=[])

        roots: List[LocationTree] = []
        for loc in snapshot.loc_by_id.values():
            tree_node = node_map[loc.id]
            if loc.parent_id and loc.parent_id in node_map:
                node_map[loc.parent_id].children.append(tree_node)
            else:
                roots.append(tree_node)
        return node_map, roots

    @classmethod
    def get_tree(cls, db: Session) -> List[LocationTree]:
        snapshot = cls.build_snapshot(db, include_counts=True)
        _, roots = cls._assemble_tree_map(snapshot)
        return roots

    @classmethod
    def get_tree_by_id(cls, db: Session, location_id: int) -> LocationTree:
        snapshot = cls.build_scoped_snapshot(
            db, [location_id], include_counts=True, include_photos=True, photos_for_descendants=True
        )
        if location_id not in snapshot.loc_by_id:
            raise NotFoundError("Location not found.")
        node_map, _ = cls._assemble_tree_map(snapshot)
        return node_map[location_id]

    @staticmethod
    def _default_slot_name(row: int, col: int, row_span: int, col_span: int) -> str:
        def col_letter(c: int) -> str:
            return chr(64 + c) if 1 <= c <= 26 else str(c)

        start = f"{col_letter(col)}{row}"
        if row_span == 1 and col_span == 1:
            return start
        end = f"{col_letter(col + col_span - 1)}{row + row_span - 1}"
        return f"{start}–{end}"

    @classmethod
    def ensure_grid_slot(
        cls,
        db: Session,
        container_id: int,
        data: GridSlotEnsureRequest,
    ) -> Tuple[Location, bool]:
        # Serialize check-then-insert so concurrent requests for one cell can't create overlapping slots.
        with _GRID_SLOT_LOCK:
            db.expire_all()
            slot, created = cls._ensure_grid_slot_locked(db, container_id, data)
            if created:
                db.commit()
            return slot, created

    @classmethod
    def _ensure_grid_slot_locked(
        cls,
        db: Session,
        container_id: int,
        data: GridSlotEnsureRequest,
    ) -> Tuple[Location, bool]:
        container = cls.get_or_404(db, container_id)
        if not (container.grid_rows and container.grid_cols):
            raise BadRequestError("Container does not have a configured grid layout.")

        end_r = data.grid_row + data.grid_row_span - 1
        end_c = data.grid_col + data.grid_col_span - 1
        if end_r > container.grid_rows or end_c > container.grid_cols:
            raise BadRequestError(
                f"Slot at ({data.grid_row}, {data.grid_col}) exceeds {container.grid_rows}x{container.grid_cols} grid."
            )

        placed_children = [
            ch for ch in (container.children or [])
            if ch.grid_row is not None and ch.grid_col is not None
        ]

        # Idempotent lookup: if an existing placed compartment already covers the target cell, return it
        for ch in placed_children:
            ch_rs = max(1, ch.grid_row_span or 1)
            ch_cs = max(1, ch.grid_col_span or 1)
            if (
                ch.grid_row <= data.grid_row <= ch.grid_row + ch_rs - 1
                and ch.grid_col <= data.grid_col <= ch.grid_col + ch_cs - 1
            ):
                return ch, False

        cls._validate_child_grid_placement(
            parent=container,
            grid_row=data.grid_row,
            grid_col=data.grid_col,
            grid_row_span=data.grid_row_span,
            grid_col_span=data.grid_col_span,
        )

        if data.location_type is not None:
            resolved_type = (
                data.location_type.value
                if hasattr(data.location_type, "value")
                else str(data.location_type)
            )
        elif placed_children:
            counts: Dict[str, int] = {}
            for ch in placed_children:
                counts[ch.location_type] = counts.get(ch.location_type, 0) + 1
            resolved_type = max(counts.items(), key=lambda kv: kv[1])[0]
        else:
            resolved_type = "drawer"

        resolved_name = (
            data.name.strip()
            if data.name and data.name.strip()
            else cls._default_slot_name(data.grid_row, data.grid_col, data.grid_row_span, data.grid_col_span)
        )
        child = Location(
            name=resolved_name,
            location_type=resolved_type,
            parent_id=container.id,
            grid_row=data.grid_row,
            grid_col=data.grid_col,
            grid_row_span=data.grid_row_span,
            grid_col_span=data.grid_col_span,
        )
        db.add(child)
        db.flush()
        db.refresh(child)
        return child, True

    @staticmethod
    def _move_items_with_audit(db: Session, from_ids: List[int], to_id: Optional[int], note: str) -> None:
        moved = db.execute(select(Item).where(Item.location_id.in_(from_ids))).scalars().all()
        now = datetime.now(timezone.utc)
        for item in moved:
            item.location_id = to_id
            item.last_seen_at = now
            item.updated_at = now
            db.add(AuditLog(item_id=item.id, location_id=to_id, action="moved", notes=note, recorded_at=now))

    @classmethod
    def save_grid_layout(cls, db: Session, location_id: int, data: LocationGridSave) -> LocationTree:
        with _GRID_SLOT_LOCK:
            return cls._save_grid_layout_locked(db, location_id, data)

    @classmethod
    def _save_grid_layout_locked(cls, db: Session, location_id: int, data: LocationGridSave) -> LocationTree:
        from sqlalchemy import update

        container = cls.get_or_404(db, location_id)

        # Validate bounds & non-overlapping cells
        occupied: Set[Tuple[int, int]] = set()
        for slot in data.slots:
            end_r = slot.grid_row + slot.grid_row_span - 1
            end_c = slot.grid_col + slot.grid_col_span - 1
            if end_r > data.grid_rows or end_c > data.grid_cols:
                raise BadRequestError(
                    f"Slot at ({slot.grid_row}, {slot.grid_col}) with span "
                    f"({slot.grid_row_span}x{slot.grid_col_span}) exceeds {data.grid_rows}x{data.grid_cols} grid."
                )
            for r in range(slot.grid_row, end_r + 1):
                for c in range(slot.grid_col, end_c + 1):
                    if (r, c) in occupied:
                        raise BadRequestError(f"Overlapping grid slot at row {r}, column {c}.")
                    occupied.add((r, c))

        existing_children: Dict[int, Location] = {ch.id: ch for ch in (container.children or [])}
        kept_ids: Set[int] = set()
        absorbed_ids: Set[int] = set()

        container.grid_rows = data.grid_rows
        container.grid_cols = data.grid_cols
        container.grid_config = data.grid_config

        # Clear placed coordinates before re-assigning so swaps/reorders don't violate the unique grid index.
        for ch in existing_children.values():
            if ch.grid_row is not None or ch.grid_col is not None:
                ch.grid_row = None
                ch.grid_col = None
        db.flush()

        for slot in data.slots:
            slot_type = slot.location_type.value if hasattr(slot.location_type, "value") else str(slot.location_type)
            slot_name = (
                slot.name.strip()
                if slot.name and slot.name.strip()
                else cls._default_slot_name(slot.grid_row, slot.grid_col, slot.grid_row_span, slot.grid_col_span)
            )
            if slot.id is not None and slot.id in existing_children:
                child = existing_children[slot.id]
                child.name = slot_name
                child.location_type = slot_type
                child.grid_row = slot.grid_row
                child.grid_col = slot.grid_col
                child.grid_row_span = slot.grid_row_span
                child.grid_col_span = slot.grid_col_span
                target_id = child.id
            else:
                child = Location(
                    name=slot_name,
                    location_type=slot_type,
                    parent_id=container.id,
                    grid_row=slot.grid_row,
                    grid_col=slot.grid_col,
                    grid_row_span=slot.grid_row_span,
                    grid_col_span=slot.grid_col_span,
                )
                db.add(child)
                db.flush()
                target_id = child.id

            kept_ids.add(target_id)

            merge_ids = [mid for mid in slot.merge_from_ids if mid in existing_children and mid != target_id]
            if merge_ids:
                cls._move_items_with_audit(db, merge_ids, target_id, "Compartments merged in grid layout")
                db.execute(update(Location).where(Location.parent_id.in_(merge_ids)).values(parent_id=target_id))
                absorbed_ids.update(merge_ids)

        # Remove any grid drawers that were absorbed or removed from the grid,
        # while preserving unplaced Box/Kit subcontainers stored directly inside this container.
        to_remove_ids = [
            cid
            for cid, ch in existing_children.items()
            if cid not in kept_ids
            and (
                cid in absorbed_ids
                or not (ch.location_type in ("box", "kit") and ch.grid_row is None and ch.grid_col is None)
            )
        ]
        unmerged_remove_ids = [cid for cid in to_remove_ids if cid not in absorbed_ids]
        if unmerged_remove_ids:
            # Preserve any items in removed drawers by moving them to the container itself
            cls._move_items_with_audit(
                db, unmerged_remove_ids, container.id, "Compartment removed from grid layout"
            )
            db.execute(
                update(Location).where(Location.parent_id.in_(unmerged_remove_ids)).values(parent_id=container.id)
            )

        removed_photo_urls: Set[str] = set()
        if to_remove_ids:
            for cid in to_remove_ids:
                child_node = existing_children[cid]
                if child_node.container_photo:
                    removed_photo_urls.add(child_node.container_photo)
                if child_node.context_photo:
                    removed_photo_urls.add(child_node.context_photo)
            gallery_paths = db.execute(
                select(LocationPhoto.photo_path).where(LocationPhoto.location_id.in_(to_remove_ids))
            ).scalars()
            for gpath in gallery_paths:
                if gpath:
                    removed_photo_urls.add(gpath)

        db.flush()
        db.expire_all()
        for cid in to_remove_ids:
            db.delete(db.get(Location, cid))

        db.flush()
        for photo_url in removed_photo_urls:
            photo_service.schedule_photo_cleanup(db, photo_url)
        db.expire_all()
        return cls.get_tree_by_id(db, container.id)

    @staticmethod
    def _normalize_coord_token(val: Optional[str]) -> str:
        return (val or "").strip().replace("\u2013", "-").replace("\u2014", "-").replace(" ", "").upper()

    @classmethod
    def _refresh_auto_coord_name(cls, slot: Location, new_row: int, new_col: int) -> None:
        if slot.grid_row is None or slot.grid_col is None:
            return
        rs = max(1, slot.grid_row_span or 1)
        cs = max(1, slot.grid_col_span or 1)
        old_coord = cls._default_slot_name(slot.grid_row, slot.grid_col, rs, cs)
        if not (slot.name or "").strip() or cls._normalize_coord_token(slot.name) == cls._normalize_coord_token(old_coord):
            slot.name = cls._default_slot_name(new_row, new_col, rs, cs)

    @staticmethod
    def _rects_overlap(
        a: Tuple[int, int, int, int],
        b: Tuple[int, int, int, int],
    ) -> bool:
        ar, ac, ars, acs = a
        br, bc, brs, bcs = b
        return ar < br + brs and ar + ars > br and ac < bc + bcs and ac + acs > bc

    @classmethod
    def move_grid_slot(
        cls,
        db: Session,
        location_id: int,
        data: GridSlotMoveRequest,
    ) -> LocationTree:
        with _GRID_SLOT_LOCK:
            return cls._move_grid_slot_locked(db, location_id, data)

    @classmethod
    def _move_grid_slot_locked(
        cls,
        db: Session,
        location_id: int,
        data: GridSlotMoveRequest,
    ) -> LocationTree:
        container = cls.get_or_404(db, location_id)
        if not (container.grid_rows and container.grid_cols):
            raise BadRequestError("Container does not have a grid layout.")

        rows = container.grid_rows
        cols = container.grid_cols
        children_by_id: Dict[int, Location] = {ch.id: ch for ch in (container.children or [])}
        src = children_by_id.get(data.source_id)
        if not src or src.grid_row is None or src.grid_col is None:
            raise NotFoundError("Source grid slot not found in this container.")

        src_rs = max(1, src.grid_row_span or 1)
        src_cs = max(1, src.grid_col_span or 1)

        def fits(rect: Tuple[int, int, int, int]) -> bool:
            r, c, rs, cs = rect
            return r >= 1 and c >= 1 and (r + rs - 1) <= rows and (c + cs - 1) <= cols

        placed_others = [
            ch
            for ch in (container.children or [])
            if ch.id != src.id
            and (data.target_id is None or ch.id != data.target_id)
            and ch.grid_row is not None
            and ch.grid_col is not None
        ]

        if data.target_id is not None:
            if data.target_id == src.id:
                return cls.get_tree_by_id(db, container.id)
            dst = children_by_id.get(data.target_id)
            if not dst or dst.grid_row is None or dst.grid_col is None:
                raise NotFoundError("Target grid slot not found in this container.")

            dst_rs = max(1, dst.grid_row_span or 1)
            dst_cs = max(1, dst.grid_col_span or 1)
            new_src_rect = (dst.grid_row, dst.grid_col, src_rs, src_cs)
            new_dst_rect = (src.grid_row, src.grid_col, dst_rs, dst_cs)

            if not fits(new_src_rect) or not fits(new_dst_rect) or cls._rects_overlap(new_src_rect, new_dst_rect):
                raise BadRequestError("Cannot swap drawers of different sizes there (not enough space).")

            for other in placed_others:
                other_rect = (
                    other.grid_row,
                    other.grid_col,
                    max(1, other.grid_row_span or 1),
                    max(1, other.grid_col_span or 1),
                )
                if cls._rects_overlap(new_src_rect, other_rect) or cls._rects_overlap(new_dst_rect, other_rect):
                    raise BadRequestError("Cannot swap there because it would overlap a neighboring drawer.")

            cls._refresh_auto_coord_name(src, new_src_rect[0], new_src_rect[1])
            cls._refresh_auto_coord_name(dst, new_dst_rect[0], new_dst_rect[1])
            src.grid_row, src.grid_col = None, None
            db.flush()
            dst.grid_row, dst.grid_col = new_dst_rect[0], new_dst_rect[1]
            db.flush()
            src.grid_row, src.grid_col = new_src_rect[0], new_src_rect[1]
        else:
            if data.target_row is None or data.target_col is None:
                raise BadRequestError("Target row and column are required when moving to an empty cell.")
            new_src_rect = (data.target_row, data.target_col, src_rs, src_cs)
            if not fits(new_src_rect):
                raise BadRequestError("Not enough space to place that drawer there.")

            for other in placed_others:
                other_rect = (
                    other.grid_row,
                    other.grid_col,
                    max(1, other.grid_row_span or 1),
                    max(1, other.grid_col_span or 1),
                )
                if cls._rects_overlap(new_src_rect, other_rect):
                    raise BadRequestError("Not enough space — overlaps a neighboring drawer.")

            cls._refresh_auto_coord_name(src, new_src_rect[0], new_src_rect[1])
            src.grid_row, src.grid_col = new_src_rect[0], new_src_rect[1]

        db.flush()
        db.expire_all()
        return cls.get_tree_by_id(db, container.id)

    @classmethod
    def generate_qr_png(
        cls,
        db: Session,
        location_id: int,
        host_header: Optional[str] = None,
        target_url: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> bytes:
        loc = cls.get_or_404(db, location_id)
        if target_url:
            resolved_target = validate_http_url(target_url, allow_scheme_less=False)
        else:
            effective_base = resolve_effective_base_url(host_header, base_url)
            if loc.location_type in ("room", "site"):
                path = f"/#/room/{location_id}"
            elif loc.location_type == "container":
                path = f"/#/unit/{location_id}"
            else:
                path = f"/#/drawer/{location_id}"
            resolved_target = f"{effective_base}{path}"

        return build_qr_png(resolved_target)

    @classmethod
    def list_photos(cls, db: Session, location_id: int) -> List[LocationPhoto]:
        cls.get_or_404(db, location_id)
        return list(
            db.execute(
                select(LocationPhoto)
                .where(LocationPhoto.location_id == location_id)
                .order_by(LocationPhoto.created_at.desc(), LocationPhoto.id.desc())
            )
            .scalars()
            .all()
        )

    @classmethod
    def attach_photo(
        cls,
        db: Session,
        location_id: int,
        photo_path: str,
        thumbnail_path: Optional[str] = None,
        caption: Optional[str] = None,
        photo_type: str = "container",
    ) -> LocationPhoto:
        loc = cls.get_or_404(db, location_id)
        photo_service.register_pending_reference(db, photo_path)
        photo = LocationPhoto(
            location_id=loc.id,
            photo_path=photo_path,
            thumbnail_path=thumbnail_path,
            caption=caption,
            photo_type=photo_type or "container",
        )
        db.add(photo)
        if photo.photo_type == "context":
            if not loc.context_photo:
                loc.context_photo = photo_path
        else:
            if not loc.container_photo:
                loc.container_photo = photo_path
        db.flush()
        db.refresh(photo)
        return photo

    @classmethod
    def add_photo(
        cls,
        db: Session,
        location_id: int,
        file: UploadFile,
        caption: Optional[str] = None,
        photo_type: str = "container",
    ) -> LocationPhoto:
        cls.get_or_404(db, location_id)
        photo_info = photo_service.process_and_save(file)
        photo_service.schedule_rollback_cleanup(
            db,
            photo_info["original_path"],
            photo_info.get("thumbnail_path"),
        )
        return cls.attach_photo(
            db=db,
            location_id=location_id,
            photo_path=photo_info["original_path"],
            thumbnail_path=photo_info.get("thumbnail_path"),
            caption=caption,
            photo_type=photo_type,
        )

    @classmethod
    def delete_photo(cls, db: Session, location_id: int, photo_id: int) -> None:
        loc = cls.get_or_404(db, location_id)
        photo = db.execute(
            select(LocationPhoto).where(
                LocationPhoto.id == photo_id,
                LocationPhoto.location_id == location_id,
            )
        ).scalar_one_or_none()
        if not photo:
            raise NotFoundError("Photo not found.")

        old_path = photo.photo_path
        db.delete(photo)
        db.flush()

        remaining = list(
            db.execute(
                select(LocationPhoto)
                .where(LocationPhoto.location_id == location_id)
                .order_by(LocationPhoto.created_at.desc(), LocationPhoto.id.desc())
            )
            .scalars()
            .all()
        )
        if loc.container_photo == old_path:
            next_container = next((p.photo_path for p in remaining if p.photo_type != "context"), None)
            loc.container_photo = next_container or (remaining[0].photo_path if remaining else None)
        if loc.context_photo == old_path:
            loc.context_photo = next((p.photo_path for p in remaining if p.photo_type == "context"), None)

        db.flush()
        photo_service.schedule_photo_cleanup(db, old_path)

    @classmethod
    def _prepare_drawer_photos_for_analysis(
        cls,
        db: Session,
        location_id: int,
        file: Optional[UploadFile] = None,
        photo_id: Optional[int] = None,
        photo_ids: Optional[str] = None,
    ) -> Tuple[List[Path], Optional[str]]:
        import re

        loc = cls.get_or_404(db, location_id)
        hint_parts: List[str] = []
        raw_name = (loc.name or "").strip()
        if raw_name and not re.match(
            r"^(drawer|bin|shelf|compartment|cubby|slot)\s+\d+$|^[A-Z]{1,2}\d+(?:[\u2013\u2014\-][A-Z]{1,2}\d+)?$",
            raw_name,
            re.IGNORECASE,
        ):
            hint_parts.append(raw_name)
        if loc.description and loc.description.strip():
            hint_parts.append(loc.description.strip()[:140])
        default_hint = " — ".join(hint_parts) if hint_parts else None

        if photo_ids and photo_ids.strip():
            raw_tokens = [tok.strip() for tok in photo_ids.split(",") if tok.strip()]
            if not raw_tokens:
                raise BadRequestError("photo_ids cannot be empty.")
            if len(raw_tokens) > 8:
                raise BadRequestError("At most 8 photos can be analyzed together for one compartment.")
            parsed_ids: List[int] = []
            for tok in raw_tokens:
                if not tok.isdigit() or int(tok) < 1:
                    raise BadRequestError("photo_ids must be a comma-separated list of positive integers.")
                pid = int(tok)
                if pid not in parsed_ids:
                    parsed_ids.append(pid)

            rows = list(
                db.execute(
                    select(LocationPhoto).where(
                        LocationPhoto.location_id == location_id,
                        LocationPhoto.id.in_(parsed_ids),
                    )
                )
                .scalars()
                .all()
            )
            by_id = {r.id: r for r in rows}
            if len(by_id) != len(parsed_ids):
                raise NotFoundError("One or more photos not found for this location.")
            return [photo_service.resolve_safe_upload_path(by_id[pid].photo_path) for pid in parsed_ids], default_hint

        if file and file.filename:
            photo_record = cls.add_photo(db=db, location_id=location_id, file=file, photo_type="container")
            photo_path = photo_record.photo_path
            db.commit()
            return [photo_service.resolve_safe_upload_path(photo_path)], default_hint
        if photo_id is not None:
            photo_record = db.execute(
                select(LocationPhoto).where(
                    LocationPhoto.id == photo_id,
                    LocationPhoto.location_id == location_id,
                )
            ).scalar_one_or_none()
            if not photo_record:
                raise NotFoundError("Photo not found.")
            return [photo_service.resolve_safe_upload_path(photo_record.photo_path)], default_hint
        if loc.container_photo:
            return [photo_service.resolve_safe_upload_path(loc.container_photo)], default_hint
        return [], default_hint

    @classmethod
    async def analyze_drawer_photo(
        cls,
        db: Session,
        location_id: int,
        file: Optional[UploadFile] = None,
        photo_id: Optional[int] = None,
        photo_ids: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        expected_count: Optional[int] = None,
        context_hint: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        bind = db.get_bind()

        def _prepare_in_thread() -> Tuple[List[Path], Optional[str]]:
            with session_scope(bind) as thread_db:
                return cls._prepare_drawer_photos_for_analysis(
                    thread_db,
                    location_id,
                    file,
                    photo_id,
                    photo_ids,
                )

        saved_photo_files, default_hint = await anyio.to_thread.run_sync(_prepare_in_thread)
        kwargs: dict = {"provider": provider, "model": model}
        if expected_count is not None:
            kwargs["expected_count"] = expected_count

        if len(saved_photo_files) <= 1 and not (photo_ids and photo_ids.strip()) and not (context_hint and context_hint.strip()):
            single_file = saved_photo_files[0] if saved_photo_files else None
            return await vision_service.analyze_image(single_file, **kwargs)

        resolved_hint = (context_hint or "").strip() or default_hint
        kwargs["context_hint"] = resolved_hint
        return await vision_service.analyze_images(saved_photo_files, **kwargs)

    @classmethod
    async def analyze_drawer_pdf(
        cls,
        db: Session,
        location_id: int,
        file: UploadFile,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        expected_count: Optional[int] = None,
        context_hint: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        import re

        bind = db.get_bind()

        def _prepare_pdf() -> Tuple[dict, Optional[str]]:
            with session_scope(bind) as thread_db:
                loc = cls.get_or_404(thread_db, location_id)
                hint_parts: List[str] = []
                raw_name = (loc.name or "").strip()
                if raw_name and not re.match(
                    r"^(drawer|bin|shelf|compartment|cubby|slot)\s+\d+$|^[A-Z]{1,2}\d+(?:[\u2013\u2014\-][A-Z]{1,2}\d+)?$",
                    raw_name,
                    re.IGNORECASE,
                ):
                    hint_parts.append(raw_name)
                if loc.description and loc.description.strip():
                    hint_parts.append(loc.description.strip()[:140])
                default_hint = " — ".join(hint_parts) if hint_parts else None
                meta = photo_service.process_and_save_attachment(file)
                if meta.get("attachment_type") != "pdf":
                    saved_p = photo_service.resolve_safe_upload_path(meta["file_path"])
                    saved_p.unlink(missing_ok=True)
                    raise BadRequestError("Please upload a valid PDF file.")
                return meta, default_hint

        meta, default_hint = await anyio.to_thread.run_sync(_prepare_pdf)
        pdf_disk_path = photo_service.resolve_safe_upload_path(meta["file_path"])
        resolved_hint = (context_hint or "").strip() or default_hint
        result = await vision_service.analyze_pdf(
            pdf_disk_path,
            filename=meta.get("filename"),
            provider=provider,
            model=model,
            expected_count=expected_count,
            context_hint=resolved_hint,
        )
        result.source_attachment_path = meta["file_path"]
        result.source_attachment_filename = meta["filename"]
        result.source_attachment_size_bytes = meta.get("size_bytes", 0)
        return result


location_service = LocationService()
