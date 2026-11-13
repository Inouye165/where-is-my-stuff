import base64
from io import BytesIO
import json
import os
from typing import Dict, List, Optional, Set, Tuple
from fastapi import UploadFile
import qrcode
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.logging_config import logger
from app.core.network import resolve_effective_base_url
from app.models.location import Location
from app.repositories.location_repository import location_repository
from app.schemas.item import DetectedItem, LocationPhotos, VisionAnalysisResponse
from app.schemas.location import Breadcrumb, LocationCreate, LocationGridSave, LocationRead, LocationTree, LocationUpdate
from app.services.photo_service import photo_service


class LocationHierarchySnapshot:
    def __init__(self, locations: List[Location], direct_counts: Dict[int, int]) -> None:
        self.loc_by_id: Dict[int, Location] = {loc.id: loc for loc in locations}
        self.children_by_parent: Dict[Optional[int], List[Location]] = {}
        for loc in locations:
            parent_key = loc.parent_id if (loc.parent_id and loc.parent_id in self.loc_by_id) else None
            self.children_by_parent.setdefault(parent_key, []).append(loc)

        self.direct_counts = direct_counts
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
            crumbs.insert(0, Breadcrumb(id=curr.id, name=curr.name, location_type=curr.location_type))
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
        return LocationRead(
            id=loc.id,
            name=loc.name,
            description=loc.description,
            location_type=loc.location_type,
            parent_id=loc.parent_id,
            container_photo=loc.container_photo,
            context_photo=loc.context_photo,
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
    def build_snapshot(db: Session, include_counts: bool = True) -> LocationHierarchySnapshot:
        locations = location_repository.list_all_ordered(db)
        counts = location_repository.get_direct_item_counts(db) if include_counts else {}
        return LocationHierarchySnapshot(locations, counts)

    @staticmethod
    def get_by_id(db: Session, location_id: int) -> Optional[Location]:
        return location_repository.get_by_id(db, location_id)

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

        snapshot = cls.build_snapshot(db, include_counts=False)
        curr_id: Optional[int] = new_parent_id
        visited: Set[int] = set()
        while curr_id is not None:
            if curr_id in visited:
                break
            visited.add(curr_id)
            if curr_id == location_id:
                raise BadRequestError("Cannot set a descendant as the parent location (circular reference).")
            node = snapshot.loc_by_id.get(curr_id)
            curr_id = node.parent_id if node else None

    @classmethod
    def create(cls, db: Session, data: LocationCreate) -> Location:
        if data.parent_id is not None:
            parent = location_repository.get_by_id(db, data.parent_id)
            if not parent:
                raise NotFoundError(f"Parent location {data.parent_id} does not exist.")

        loc_type_val = data.location_type.value if hasattr(data.location_type, "value") else str(data.location_type)
        loc = Location(
            name=data.name.strip(),
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

        for key, value in update_dict.items():
            if key == "name" and value is not None:
                setattr(loc, key, value.strip())
            elif key == "location_type" and value is not None:
                setattr(loc, key, value.value if hasattr(value, "value") else str(value))
            else:
                setattr(loc, key, value)

        db.flush()
        db.refresh(loc)

        for old_photo in old_photos:
            if old_photo and old_photo not in (loc.container_photo, loc.context_photo):
                photo_service.cleanup_unreferenced_photo(db, old_photo)

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

        from sqlalchemy import update
        from app.models.item import Item

        # Preserve containers (boxes, labels, drawers, items) in "Needs a home"
        if detached_container_ids:
            db.execute(
                update(Location)
                .where(Location.id.in_(detached_container_ids))
                .values(parent_id=None)
            )

        # Preserve any loose items in deleted nodes in "Needs a home"
        if deleted_node_ids:
            db.execute(
                update(Item)
                .where(Item.location_id.in_(deleted_node_ids))
                .values(location_id=None)
            )

        db.flush()
        db.expire_all()
        loc_to_delete = db.get(Location, location_id)
        if loc_to_delete is not None:
            db.delete(loc_to_delete)
            db.flush()

        for photo_url in candidate_photos:
            photo_service.cleanup_unreferenced_photo(db, photo_url)

    @classmethod
    def get_read_by_id(cls, db: Session, location_id: int) -> LocationRead:
        snapshot = cls.build_snapshot(db, include_counts=True)
        loc = snapshot.loc_by_id.get(location_id)
        if not loc:
            raise NotFoundError("Location not found.")
        return snapshot.to_read(loc)

    @classmethod
    def to_read_schema(cls, loc: Location, db: Optional[Session] = None) -> LocationRead:
        if db is not None:
            return cls.get_read_by_id(db, loc.id)

        crumbs = [Breadcrumb(**b) for b in loc.get_breadcrumbs()]
        direct_items = len(loc.items) if loc.items else 0
        children_count = len(loc.children) if loc.children else 0
        total_items = cls._count_total_items_fallback(loc)
        return LocationRead(
            id=loc.id,
            name=loc.name,
            description=loc.description,
            location_type=loc.location_type,
            parent_id=loc.parent_id,
            container_photo=loc.container_photo,
            context_photo=loc.context_photo,
            grid_rows=loc.grid_rows,
            grid_cols=loc.grid_cols,
            grid_config=loc.grid_config,
            grid_row=loc.grid_row,
            grid_col=loc.grid_col,
            grid_row_span=loc.grid_row_span,
            grid_col_span=loc.grid_col_span,
            full_path=loc.full_path,
            breadcrumbs=crumbs,
            item_count=direct_items,
            total_item_count=total_items,
            child_count=children_count,
            created_at=loc.created_at,
            updated_at=loc.updated_at,
        )

    @classmethod
    def _count_total_items_fallback(cls, loc: Location, visited: Optional[Set[int]] = None) -> int:
        if visited is None:
            visited = set()
        if loc.id in visited:
            return 0
        visited.add(loc.id)
        total = len(loc.items) if loc.items else 0
        for child in loc.children or []:
            total += cls._count_total_items_fallback(child, visited)
        return total

    @classmethod
    def get_all(cls, db: Session) -> List[LocationRead]:
        snapshot = cls.build_snapshot(db, include_counts=True)
        return [snapshot.to_read(loc) for loc in snapshot.loc_by_id.values()]

    @classmethod
    def get_tree(cls, db: Session) -> List[LocationTree]:
        snapshot = cls.build_snapshot(db, include_counts=True)
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
        return roots

    @classmethod
    def get_tree_by_id(cls, db: Session, location_id: int) -> LocationTree:
        snapshot = cls.build_snapshot(db, include_counts=True)
        if location_id not in snapshot.loc_by_id:
            raise NotFoundError("Location not found.")
        node_map: Dict[int, LocationTree] = {}
        for loc in snapshot.loc_by_id.values():
            read_model = snapshot.to_read(loc)
            node_map[loc.id] = LocationTree(**read_model.model_dump(), children=[])
        for loc in snapshot.loc_by_id.values():
            if loc.parent_id and loc.parent_id in node_map:
                node_map[loc.parent_id].children.append(node_map[loc.id])
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
    def save_grid_layout(cls, db: Session, location_id: int, data: LocationGridSave) -> LocationTree:
        from sqlalchemy import update
        from app.models.item import Item

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
                db.execute(update(Item).where(Item.location_id.in_(merge_ids)).values(location_id=target_id))
                db.execute(update(Location).where(Location.parent_id.in_(merge_ids)).values(parent_id=target_id))
                absorbed_ids.update(merge_ids)

        # Remove any drawers that were absorbed or removed from the grid
        to_remove_ids = [cid for cid in existing_children if cid not in kept_ids]
        unmerged_remove_ids = [cid for cid in to_remove_ids if cid not in absorbed_ids]
        if unmerged_remove_ids:
            # Preserve any items in removed drawers by moving them to the container itself
            db.execute(update(Item).where(Item.location_id.in_(unmerged_remove_ids)).values(location_id=container.id))
            db.execute(
                update(Location).where(Location.parent_id.in_(unmerged_remove_ids)).values(parent_id=container.id)
            )

        for cid in to_remove_ids:
            db.delete(existing_children[cid])

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
        resolved_target = target_url
        if not resolved_target:
            effective_base = resolve_effective_base_url(host_header, base_url)
            if loc.location_type in ("room", "site"):
                path = f"/#/room/{location_id}"
            elif loc.location_type == "container":
                path = f"/#/unit/{location_id}"
            else:
                path = f"/#/drawer/{location_id}"
            resolved_target = f"{effective_base}{path}"

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(resolved_target)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @classmethod
    async def analyze_drawer_photo(
        cls,
        db: Session,
        location_id: int,
        file: Optional[UploadFile] = None,
    ) -> VisionAnalysisResponse:
        loc = cls.get_or_404(db, location_id)

        saved_photo_file = None
        if file and file.filename:
            old_container_photo = loc.container_photo
            photo_info = photo_service.process_and_save(file)
            loc.container_photo = photo_info["original_path"]
            db.flush()
            if old_container_photo and old_container_photo != loc.container_photo:
                photo_service.cleanup_unreferenced_photo(db, old_container_photo)
            saved_photo_file = photo_service.resolve_safe_upload_path(photo_info["original_path"])

        gemini_key = os.environ.get("GEMINI_API_KEY") or settings.GEMINI_API_KEY
        if not gemini_key or not saved_photo_file or not saved_photo_file.exists():
            return VisionAnalysisResponse(
                items=[],
                has_vision_api=bool(gemini_key),
                message="Photo uploaded. Enter items line-by-line below to batch-add them to this drawer.",
            )

        try:
            import httpx

            b64_data = base64.b64encode(saved_photo_file.read_bytes()).decode("utf-8")
            prompt = (
                "Analyze this photo of an open storage drawer/bin. "
                "List all visible tools, parts, hardware components, or items. "
                "Return valid JSON matching: "
                '{"items": [{"name": "...", "category": "...", "estimated_qty": 1, "brief_specs": "..."}]}'
            )
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={gemini_key}"
            )
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}},
                        ]
                    }
                ],
                "generationConfig": {"response_mime_type": "application/json"},
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    resp_data = resp.json()
                    text_content = resp_data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_content)
                    detected = [DetectedItem(**it) for it in parsed.get("items", [])]
                    return VisionAnalysisResponse(
                        status="success",
                        items=detected,
                        has_vision_api=True,
                        message=f"Vision AI detected {len(detected)} items in drawer.",
                    )
        except Exception as exc:
            logger.warning("Optional Gemini vision analysis failed for location %d: %s", location_id, exc)

        return VisionAnalysisResponse(
            items=[],
            has_vision_api=True,
            message="Vision query finished. You can enter or confirm items below.",
        )


location_service = LocationService()
