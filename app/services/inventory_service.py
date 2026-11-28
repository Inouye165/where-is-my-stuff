from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.audit import AuditLog
from app.models.item import Item, ItemAttachment
from app.models.location import Location, LocationPhoto
from app.repositories.item_repository import item_repository
from app.repositories.location_repository import location_repository
from app.schemas.audit import AuditLogRead
from app.schemas.common import ItemSortOrder
from app.schemas.item import (
    ItemAttachmentRead,
    ItemBulkCreate,
    ItemConvertToBoxRequest,
    ItemCreate,
    ItemDetailRead,
    ItemGroupIntoBoxRequest,
    ItemGroupIntoBoxResponse,
    ItemImagesToPdfRequest,
    ItemPageResponse,
    ItemPhotoAttachmentCreate,
    ItemRead,
    ItemUpdate,
    LocationPhotos,
)
from app.services.location_service import LocationHierarchySnapshot, location_service
from app.services.photo_service import photo_service
from app.services.quick_entry_parser import parse_quick_entry_lines as _parse_quick_entry_lines


class InventoryService:
    @classmethod
    def _serialize_with_snapshot(cls, item: Item, snapshot: LocationHierarchySnapshot) -> ItemRead:
        loc_id = item.location_id
        loc = snapshot.loc_by_id.get(loc_id) if loc_id is not None else None
        crumbs = snapshot.breadcrumbs_for(loc.id) if loc else []
        loc_photos = snapshot.inherited_photos_for(loc.id) if loc else LocationPhotos()

        return ItemRead(
            id=item.id,
            name=item.name,
            description=item.description,
            category=item.category,
            tags=item.tags,
            quantity=item.quantity,
            unit=item.unit,
            serial_or_part_number=item.serial_or_part_number,
            item_photo=item.item_photo,
            status=getattr(item, "status", "stored") or "stored",
            status_note=getattr(item, "status_note", None),
            location_id=item.location_id,
            last_seen_at=item.last_seen_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
            location_name=loc.name if loc else None,
            location_full_path=snapshot.full_path_for(loc.id) if loc else None,
            location_breadcrumbs=crumbs,
            location_photos=loc_photos,
        )

    @classmethod
    def serialize_many(cls, db: Session, items: List[Item]) -> List[ItemRead]:
        if not items:
            return []
        loc_ids = [it.location_id for it in items if it.location_id is not None]
        snapshot = location_service.build_scoped_snapshot(
            db, loc_ids, include_counts=False, include_photos=False
        )
        return [cls._serialize_with_snapshot(it, snapshot) for it in items]

    @classmethod
    def to_read_schema(cls, item: Item, db: Session) -> ItemRead:
        loc_ids = [item.location_id] if item.location_id is not None else []
        snapshot = location_service.build_scoped_snapshot(
            db, loc_ids, include_counts=False, include_photos=False
        )
        return cls._serialize_with_snapshot(item, snapshot)

    @classmethod
    def to_detail_schema(cls, item: Item, db: Session) -> ItemDetailRead:
        base = cls.to_read_schema(item, db=db)
        logs = [
            AuditLogRead(
                id=log.id,
                item_id=log.item_id,
                location_id=log.location_id,
                action=log.action,
                notes=log.notes,
                recorded_at=log.recorded_at,
            )
            for log in item.audit_logs
        ]
        attachments = [ItemAttachmentRead.model_validate(a) for a in (item.attachments or [])]
        return ItemDetailRead(**base.model_dump(), audit_logs=logs, attachments=attachments)

    @classmethod
    def list_items(
        cls,
        db: Session,
        category: Optional[str] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        sort: ItemSortOrder = ItemSortOrder.last_seen_desc,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ItemRead]:
        items = item_repository.list_filtered(
            db,
            category=category,
            location_id=location_id,
            status=status,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return cls.serialize_many(db, items)

    @classmethod
    def list_items_page(
        cls,
        db: Session,
        category: Optional[str] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        sort: ItemSortOrder = ItemSortOrder.last_seen_desc,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> ItemPageResponse:
        rows, total, next_cursor, has_more = item_repository.list_page(
            db,
            category=category,
            location_id=location_id,
            status=status,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
        return ItemPageResponse(
            items=cls.serialize_many(db, rows),
            total=total,
            limit=limit,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @classmethod
    def list_items_for_location(cls, db: Session, location_id: int) -> List[ItemRead]:
        loc = location_repository.get_by_id(db, location_id)
        if not loc:
            raise NotFoundError("Location not found.")
        items = item_repository.list_by_location(db, location_id)
        return cls.serialize_many(db, items)

    @classmethod
    def get_detail(cls, db: Session, item_id: int) -> ItemDetailRead:
        item = item_repository.get_by_id(db, item_id, include_audits=True)
        if not item:
            raise NotFoundError("Item not found.")
        return cls.to_detail_schema(item, db=db)

    @classmethod
    def create(cls, db: Session, data: ItemCreate) -> Item:
        if data.location_id is not None:
            loc = location_repository.get_by_id(db, data.location_id)
            if not loc:
                raise NotFoundError(f"Location {data.location_id} does not exist.")

        now = datetime.now(timezone.utc)
        photo_service.register_pending_reference(db, data.item_photo)
        status_val = data.status.value if hasattr(data.status, "value") else (data.status or "stored")
        item = Item(
            name=data.name.strip(),
            description=data.description,
            category=data.category.strip() if data.category else None,
            tags=data.tags.strip() if data.tags else None,
            quantity=data.quantity,
            unit=data.unit,
            serial_or_part_number=data.serial_or_part_number,
            item_photo=data.item_photo,
            status=status_val,
            status_note=data.status_note,
            location_id=data.location_id,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        db.flush()

        audit = AuditLog(
            item_id=item.id,
            location_id=item.location_id,
            action="created",
            notes="Item initially recorded in inventory",
            recorded_at=now,
        )
        db.add(audit)
        db.flush()
        db.refresh(item)
        return item

    @classmethod
    def parse_quick_entry_lines(cls, text: str, location_id: int) -> List[ItemCreate]:
        return _parse_quick_entry_lines(text, location_id)

    @staticmethod
    def _build_assortment_tags(items: List[ItemCreate]) -> Optional[str]:
        seen: set[str] = set()
        tokens: List[str] = []
        for it in items:
            candidates: List[str] = []
            if it.serial_or_part_number and it.serial_or_part_number.strip():
                candidates.append(it.serial_or_part_number.strip())
            if it.name and it.name.strip():
                candidates.append(it.name.strip())
            if it.category and it.category.strip():
                candidates.append(it.category.strip())
            for cand in candidates:
                norm = cand.lower()
                if norm not in seen:
                    seen.add(norm)
                    tokens.append(cand)
        acc = ""
        for tok in tokens:
            nxt = f"{acc}, {tok}" if acc else tok
            if len(nxt) > 300:
                break
            acc = nxt
        return acc or None

    @classmethod
    def bulk_create(cls, db: Session, data: ItemBulkCreate) -> List[Item]:
        loc = location_repository.get_by_id(db, data.location_id)
        if not loc:
            raise NotFoundError(f"Location {data.location_id} not found.")

        items_to_create: List[ItemCreate] = []
        if data.items:
            for it in data.items:
                items_to_create.append(it.model_copy(update={"location_id": data.location_id}))

        raw_input = data.raw_lines or data.raw_text
        if raw_input:
            parsed = cls.parse_quick_entry_lines(raw_input, data.location_id)
            items_to_create.extend(parsed)

        default_cat = data.category.strip() if data.category and data.category.strip() else None
        if default_cat:
            for it in items_to_create:
                if not it.category:
                    it.category = default_cat

        if not items_to_create:
            raise BadRequestError("No items provided to create.")

        if data.save_mode == "box":
            group_name = (data.assortment_name or "").strip()[:120]
            if not group_name:
                group_name = (f"Assorted {default_cat}" if default_cat else f"Box in {loc.name}")[:120]
            group_photo = data.assortment_photo
            photo_service.register_pending_reference(db, group_photo)
            group_label = (data.assortment_label or "").strip()[:40] or None
            child_box = Location(
                name=group_name,
                label=group_label,
                location_type="box",
                parent_id=loc.id,
                container_photo=group_photo,
            )
            db.add(child_box)
            db.flush()

            reassigned_paths: set[str] = set()
            if data.source_photo_ids:
                src_ids = [int(pid) for pid in data.source_photo_ids if pid]
                if src_ids:
                    src_rows = list(
                        db.execute(
                            select(LocationPhoto)
                            .where(
                                LocationPhoto.location_id == loc.id,
                                LocationPhoto.id.in_(src_ids),
                            )
                            .order_by(LocationPhoto.id.asc())
                        )
                        .scalars()
                        .all()
                    )
                    for row in src_rows:
                        row.location_id = child_box.id
                        if row.photo_path:
                            reassigned_paths.add(row.photo_path)
                    if src_rows and not child_box.container_photo:
                        child_box.container_photo = src_rows[0].photo_path
                    db.flush()

                    if reassigned_paths:
                        remaining_parent_photos = list(
                            db.execute(
                                select(LocationPhoto)
                                .where(LocationPhoto.location_id == loc.id)
                                .order_by(LocationPhoto.created_at.desc(), LocationPhoto.id.desc())
                            )
                            .scalars()
                            .all()
                        )
                        if loc.container_photo in reassigned_paths:
                            next_cont = next(
                                (p.photo_path for p in remaining_parent_photos if p.photo_type != "context"),
                                None,
                            )
                            loc.container_photo = next_cont or (
                                remaining_parent_photos[0].photo_path if remaining_parent_photos else None
                            )
                        if loc.context_photo in reassigned_paths:
                            loc.context_photo = next(
                                (p.photo_path for p in remaining_parent_photos if p.photo_type == "context"),
                                None,
                            )
                        db.flush()

            if group_photo and group_photo not in reassigned_paths:
                thumb = (
                    f"/uploads/thumbnails/{Path(group_photo).stem}_thumb.webp"
                    if group_photo.startswith("/uploads/originals/")
                    else None
                )
                location_service.attach_photo(
                    db=db,
                    location_id=child_box.id,
                    photo_path=group_photo,
                    thumbnail_path=thumb,
                    photo_type="container",
                )

            box_items = [
                cls.create(db, it_data.model_copy(update={"location_id": child_box.id}))
                for it_data in items_to_create
            ]
            cls._link_source_attachment(db, box_items, data)
            db.flush()
            return box_items

        if data.save_mode == "assortment":
            part_lines: List[str] = []
            for it in items_to_create:
                qty_prefix = f"{it.quantity}x " if (it.quantity is not None and it.quantity > 0) else ""
                part_suffix = ""
                if it.serial_or_part_number and it.serial_or_part_number.strip():
                    pn = it.serial_or_part_number.strip()
                    if pn.lower() not in it.name.lower():
                        part_suffix = f" [{pn}]"
                spec_suffix = ""
                if it.description and it.description.strip():
                    spec_suffix = f" — {it.description.strip()}"
                part_lines.append(f"• {qty_prefix}{it.name.strip()}{part_suffix}{spec_suffix}")

            description_text = ("Parts List:\n" + "\n".join(part_lines))[:20_000]
            group_name = (data.assortment_name or "").strip()[:160]
            if not group_name:
                group_name = (f"Assorted {default_cat}" if default_cat else f"Assorted Parts ({loc.name})")[:160]
            group_cat = default_cat or next((it.category for it in items_to_create if it.category), None)
            group_photo = data.assortment_photo or next((it.item_photo for it in items_to_create if it.item_photo), None)
            tags_str = cls._build_assortment_tags(items_to_create)

            kit_data = ItemCreate(
                name=group_name,
                description=description_text,
                category=group_cat,
                tags=tags_str,
                quantity=1,
                unit="kit",
                item_photo=group_photo,
                location_id=data.location_id,
                status="stored",
            )
            created_kit = cls.create(db, kit_data)
            cls._link_source_attachment(db, [created_kit], data)
            db.flush()
            return [created_kit]

        created_items = [cls.create(db, it_data) for it_data in items_to_create]
        cls._link_source_attachment(db, created_items, data)
        db.flush()
        return created_items

    @classmethod
    def _link_source_attachment(cls, db: Session, items: List[Item], data: ItemBulkCreate) -> None:
        if not items or not data.source_attachment_path:
            return
        disk_path = photo_service.resolve_safe_upload_path(data.source_attachment_path)
        if not disk_path.exists() or not disk_path.is_file():
            return
        ext = disk_path.suffix.lower()
        att_type = "pdf" if ext == ".pdf" else ("image" if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"} else "document")
        content_type = "application/pdf" if ext == ".pdf" else None
        filename = (data.source_attachment_filename or disk_path.name).strip()[:255] or disk_path.name
        title = (data.source_attachment_title or "").strip()[:200] or None
        size_bytes = disk_path.stat().st_size
        photo_service.register_pending_reference(db, data.source_attachment_path)
        for item in items:
            db.add(
                ItemAttachment(
                    item_id=item.id,
                    file_path=data.source_attachment_path,
                    thumbnail_path=None,
                    filename=filename,
                    content_type=content_type,
                    attachment_type=att_type,
                    title=title,
                    size_bytes=size_bytes,
                )
            )

    @classmethod
    def group_items_into_box(cls, db: Session, data: ItemGroupIntoBoxRequest) -> ItemGroupIntoBoxResponse:
        parent = location_repository.get_by_id(db, data.parent_location_id)
        if not parent:
            raise NotFoundError(f"Parent location {data.parent_location_id} not found.")

        unique_ids = list(dict.fromkeys(data.item_ids))
        items = list(db.execute(select(Item).where(Item.id.in_(unique_ids))).scalars().all())
        if not items:
            raise BadRequestError("No matching items found to move.")

        if data.target_box_id is not None:
            box = location_repository.get_by_id(db, data.target_box_id)
            if not box:
                raise NotFoundError(f"Target box {data.target_box_id} not found.")
        else:
            raw_name = (data.box_name or "").strip()[:120]
            if not raw_name:
                raise BadRequestError("Please enter a Box or Kit name.")
            photo = data.box_photo or next((it.item_photo for it in items if it.item_photo), None)
            photo_service.register_pending_reference(db, photo)
            box = Location(
                name=raw_name,
                location_type="box",
                parent_id=parent.id,
                container_photo=photo,
            )
            db.add(box)
            db.flush()
            if photo:
                thumb = (
                    f"/uploads/thumbnails/{Path(photo).stem}_thumb.webp"
                    if photo.startswith("/uploads/originals/")
                    else None
                )
                location_service.attach_photo(
                    db=db,
                    location_id=box.id,
                    photo_path=photo,
                    thumbnail_path=thumb,
                    photo_type="container",
                )

        now = datetime.now(timezone.utc)
        for item in items:
            if item.location_id != box.id:
                item.location_id = box.id
                item.last_seen_at = now
                item.updated_at = now
                db.add(
                    AuditLog(
                        item_id=item.id,
                        location_id=box.id,
                        action="moved",
                        notes=f"Moved into {box.name}",
                        recorded_at=now,
                    )
                )

        db.flush()
        box_read = location_service.get_read_by_id(db, box.id)
        return ItemGroupIntoBoxResponse(box=box_read, moved_count=len(items))

    @classmethod
    def update(cls, db: Session, item_id: int, data: ItemUpdate) -> Item:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")

        old_photo = item.item_photo
        update_dict = data.model_dump(exclude_unset=True)
        now = datetime.now(timezone.utc)
        photo_service.register_pending_reference(db, update_dict.get("item_photo"))

        if "status" in update_dict and update_dict["status"] is not None:
            raw_status = update_dict["status"]
            new_status = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
            update_dict["status"] = new_status
            old_status = getattr(item, "status", "stored")
            if new_status != old_status:
                note = update_dict.get("status_note", item.status_note) or ""
                if new_status == "in_use":
                    db.add(
                        AuditLog(
                            item_id=item.id,
                            location_id=item.location_id,
                            action="checked_out",
                            notes=f"Pulled to workbench. {note}".strip(),
                            recorded_at=now,
                        )
                    )
                elif new_status == "stored":
                    db.add(
                        AuditLog(
                            item_id=item.id,
                            location_id=item.location_id,
                            action="returned",
                            notes=f"Returned to bin. {note}".strip(),
                            recorded_at=now,
                        )
                    )
                    item.last_seen_at = now

        if "location_id" in update_dict:
            new_loc_id = update_dict["location_id"]
            if new_loc_id != item.location_id:
                if new_loc_id is not None:
                    loc = location_repository.get_by_id(db, new_loc_id)
                    if not loc:
                        raise NotFoundError(f"Target location {new_loc_id} does not exist.")
                    loc_name = loc.name
                else:
                    loc_name = "Unassigned"

                db.add(
                    AuditLog(
                        item_id=item.id,
                        location_id=new_loc_id,
                        action="moved",
                        notes=f"Relocated to: {loc_name}",
                        recorded_at=now,
                    )
                )
                item.last_seen_at = now

        for key, value in update_dict.items():
            if key in ("name", "category", "tags") and isinstance(value, str):
                setattr(item, key, value.strip() or (None if key != "name" else value.strip()))
            else:
                setattr(item, key, value)

        item.updated_at = now
        db.flush()
        db.refresh(item)

        if old_photo and old_photo != item.item_photo:
            photo_service.schedule_photo_cleanup(db, old_photo)

        return item

    @classmethod
    def verify_presence(
        cls,
        db: Session,
        item_id: int,
        notes: Optional[str] = None,
        new_location_id: Optional[int] = None,
    ) -> Item:
        item = item_repository.get_by_id(db, item_id, include_audits=True)
        if not item:
            raise NotFoundError("Item not found.")

        now = datetime.now(timezone.utc)
        if new_location_id is not None and new_location_id != item.location_id:
            loc = location_repository.get_by_id(db, new_location_id)
            if not loc:
                raise NotFoundError(f"Location {new_location_id} not found.")
            item.location_id = new_location_id
            action = "moved_and_verified"
            audit_notes = notes or f"Verified present at updated location: {loc.name}"
        else:
            action = "verified_present"
            audit_notes = notes or "Physical presence verified by user"

        item.last_seen_at = now
        item.updated_at = now
        db.add(
            AuditLog(
                item_id=item.id,
                location_id=item.location_id,
                action=action,
                notes=audit_notes,
                recorded_at=now,
            )
        )
        db.flush()
        db.expire(item, ["audit_logs"])
        db.refresh(item)
        return item

    @classmethod
    def delete(cls, db: Session, item_id: int) -> None:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")
        old_photo = item.item_photo
        attachment_files = list(
            db.execute(
                select(ItemAttachment.file_path, ItemAttachment.thumbnail_path).where(
                    ItemAttachment.item_id == item_id
                )
            ).all()
        )
        db.delete(item)
        db.flush()
        if old_photo:
            photo_service.schedule_photo_cleanup(db, old_photo)
        for file_path, thumb_path in attachment_files:
            photo_service.schedule_attachment_cleanup(db, file_path, thumb_path)

    @classmethod
    def list_attachments(cls, db: Session, item_id: int) -> List[ItemAttachment]:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")
        return list(
            db.execute(
                select(ItemAttachment)
                .where(ItemAttachment.item_id == item_id)
                .order_by(ItemAttachment.created_at.desc(), ItemAttachment.id.desc())
            )
            .scalars()
            .all()
        )

    @classmethod
    def add_attachment(
        cls,
        db: Session,
        item_id: int,
        file: UploadFile,
        title: Optional[str] = None,
    ) -> ItemAttachment:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")
        meta = photo_service.process_and_save_attachment(file)
        photo_service.schedule_rollback_cleanup(db, meta["file_path"], meta.get("thumbnail_path"))
        att = ItemAttachment(
            item_id=item.id,
            file_path=meta["file_path"],
            thumbnail_path=meta.get("thumbnail_path"),
            filename=meta["filename"],
            content_type=meta.get("content_type"),
            attachment_type=meta["attachment_type"],
            title=(title or "").strip()[:200] or None,
            size_bytes=meta.get("size_bytes", 0),
        )
        db.add(att)
        if att.attachment_type == "image" and not item.item_photo:
            item.item_photo = att.file_path
        db.flush()
        db.refresh(att)
        return att

    @classmethod
    def add_photo_attachment(
        cls,
        db: Session,
        item_id: int,
        data: ItemPhotoAttachmentCreate,
    ) -> ItemAttachment:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")
        disk_path = photo_service.resolve_safe_upload_path(data.photo_path)
        if not disk_path.exists() or not disk_path.is_file():
            raise NotFoundError("Uploaded photo file not found.")

        thumb_path = data.thumbnail_path
        if not thumb_path and data.photo_path.startswith("/uploads/originals/"):
            candidate_thumb = f"/uploads/thumbnails/{disk_path.stem}_thumb.webp"
            if photo_service.resolve_safe_upload_path(candidate_thumb).exists():
                thumb_path = candidate_thumb

        photo_service.register_pending_reference(db, data.photo_path)
        existing = db.execute(
            select(ItemAttachment).where(
                ItemAttachment.item_id == item.id,
                ItemAttachment.file_path == data.photo_path,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if data.title and not existing.title:
                existing.title = data.title.strip()[:200] or None
            if data.set_as_main or not item.item_photo:
                item.item_photo = data.photo_path
            db.flush()
            db.refresh(existing)
            return existing

        ext = disk_path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }
        att = ItemAttachment(
            item_id=item.id,
            file_path=data.photo_path,
            thumbnail_path=thumb_path,
            filename=disk_path.name,
            content_type=mime_map.get(ext, "image/jpeg"),
            attachment_type="image",
            title=(data.title or "").strip()[:200] or None,
            size_bytes=disk_path.stat().st_size,
        )
        db.add(att)
        if data.set_as_main or not item.item_photo:
            item.item_photo = data.photo_path
        db.flush()
        db.refresh(att)
        return att

    @classmethod
    def create_pdf_attachment_from_photos(
        cls,
        db: Session,
        item_id: int,
        data: ItemImagesToPdfRequest,
    ) -> ItemAttachment:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")

        clean_title = (data.title or "").strip()[:160] or f"{item.name} Document"[:160]
        meta = photo_service.convert_photos_to_pdf_attachment(
            photo_paths=data.photo_paths,
            filename=clean_title,
            max_dimension=data.max_dimension,
        )
        photo_service.schedule_rollback_cleanup(db, meta["file_path"])
        att = ItemAttachment(
            item_id=item.id,
            file_path=meta["file_path"],
            thumbnail_path=None,
            filename=meta["filename"],
            content_type=meta["content_type"],
            attachment_type=meta["attachment_type"],
            title=clean_title,
            size_bytes=meta.get("size_bytes", 0),
        )
        db.add(att)
        db.flush()

        if data.remove_source_photos and item.location_id:
            loc_photos = list(
                db.execute(
                    select(LocationPhoto).where(
                        LocationPhoto.location_id == item.location_id,
                        LocationPhoto.photo_path.in_(data.photo_paths),
                    )
                )
                .scalars()
                .all()
            )
            for lp in loc_photos:
                if lp.photo_path != item.item_photo:
                    location_service.delete_photo(db, item.location_id, lp.id)

        db.refresh(att)
        return att

    @classmethod
    def delete_attachment(cls, db: Session, item_id: int, attachment_id: int) -> None:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")
        att = db.execute(
            select(ItemAttachment).where(
                ItemAttachment.id == attachment_id,
                ItemAttachment.item_id == item_id,
            )
        ).scalar_one_or_none()
        if not att:
            raise NotFoundError("Attachment not found.")
        old_path = att.file_path
        old_thumb = att.thumbnail_path
        db.delete(att)
        if item.item_photo == old_path:
            item.item_photo = None
        db.flush()
        photo_service.schedule_attachment_cleanup(db, old_path, old_thumb)

    @classmethod
    def convert_item_to_box(
        cls,
        db: Session,
        item_id: int,
        data: ItemConvertToBoxRequest,
    ):
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")
        box_name = (data.box_name or item.name).strip()[:120]
        if not box_name:
            raise BadRequestError("Box name cannot be empty.")

        clean_label = (data.box_label or "").strip()[:40] or None
        photo_service.register_pending_reference(db, item.item_photo)
        box = Location(
            name=box_name,
            label=clean_label,
            description=item.description,
            location_type="box",
            parent_id=item.location_id,
            container_photo=item.item_photo,
        )
        db.add(box)
        db.flush()

        if item.item_photo:
            stem = Path(item.item_photo).stem
            thumb_rel = f"/uploads/thumbnails/{stem}_thumb.webp"
            db.add(
                LocationPhoto(
                    location_id=box.id,
                    photo_path=item.item_photo,
                    thumbnail_path=thumb_rel if item.item_photo.startswith("/uploads/originals/") else None,
                    photo_type="container",
                )
            )

        if data.keep_item_inside:
            item.location_id = box.id
            item.last_seen_at = datetime.now(timezone.utc)
            db.add(
                AuditLog(
                    item_id=item.id,
                    location_id=box.id,
                    action="moved",
                    notes=f"Moved into box '{box_name}'",
                )
            )
        else:
            # Delete directly so item.item_photo (now owned by box) is NOT scheduled for orphan cleanup.
            for att in list(item.attachments or []):
                photo_service.schedule_attachment_cleanup(db, att.file_path, att.thumbnail_path)
            db.delete(item)

        db.flush()
        return location_service.get_read_by_id(db, box.id)


inventory_service = InventoryService()
