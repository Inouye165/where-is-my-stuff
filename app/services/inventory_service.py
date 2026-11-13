from datetime import datetime, timezone
import re
from typing import List, Optional
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.audit import AuditLog
from app.models.item import Item, ItemAttachment
from app.models.location import Location
from app.repositories.item_repository import item_repository
from app.repositories.location_repository import location_repository
from app.schemas.audit import AuditLogRead
from app.schemas.common import ItemSortOrder
from app.schemas.item import ItemAttachmentRead, ItemBulkCreate, ItemCreate, ItemDetailRead, ItemRead, ItemUpdate, LocationPhotos
from app.schemas.location import Breadcrumb
from app.services.location_service import LocationHierarchySnapshot, location_service
from app.services.photo_service import photo_service


class InventoryService:
    @staticmethod
    def _resolve_location_photos_fallback(loc: Optional[Location]) -> LocationPhotos:
        if not loc:
            return LocationPhotos()

        container_photo: Optional[str] = None
        context_photo: Optional[str] = None
        curr = loc
        visited: set[int] = set()
        while curr and curr.id not in visited:
            visited.add(curr.id)
            if not container_photo and curr.container_photo:
                container_photo = curr.container_photo
            if not context_photo and curr.context_photo:
                context_photo = curr.context_photo
            curr = curr.parent

        return LocationPhotos(container_photo=container_photo, context_photo=context_photo)

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
        snapshot = location_service.build_snapshot(db, include_counts=False)
        return [cls._serialize_with_snapshot(it, snapshot) for it in items]

    @classmethod
    def to_read_schema(cls, item: Item, db: Optional[Session] = None) -> ItemRead:
        if db is not None:
            snapshot = location_service.build_snapshot(db, include_counts=False)
            return cls._serialize_with_snapshot(item, snapshot)

        loc = item.location
        crumbs = [Breadcrumb(**b) for b in loc.get_breadcrumbs()] if loc else []
        loc_photos = cls._resolve_location_photos_fallback(loc)

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
            location_full_path=loc.full_path if loc else None,
            location_breadcrumbs=crumbs,
            location_photos=loc_photos,
        )

    @classmethod
    def to_detail_schema(cls, item: Item, db: Optional[Session] = None) -> ItemDetailRead:
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
    ) -> List[ItemRead]:
        items = item_repository.list_filtered(
            db, category=category, location_id=location_id, status=status, sort=sort, limit=limit
        )
        return cls.serialize_many(db, items)

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
        items: List[ItemCreate] = []
        if not text:
            return items

        unit_pattern = r"pcs|pieces|box|boxes|set|sets|kit|kits|pack|packs|rolls?|tubes?|ft|meters?"
        for raw in text.splitlines():
            line = raw.strip()
            line = re.sub(r"^(?:\d+[\.\)]\s*|[-*•]\s*)", "", line).strip()
            if not line:
                continue

            qty = 1
            unit = "pcs"
            name = line

            m1 = re.match(r"^(\d+)\s*[xX]\s+(.*)$", line)
            if m1:
                qty = int(m1.group(1))
                name = m1.group(2).strip()
            else:
                m2 = re.match(rf"^(\d+)\s+({unit_pattern})\s+(.*)$", line, re.IGNORECASE)
                if m2:
                    qty = int(m2.group(1))
                    unit = m2.group(2).lower()
                    name = m2.group(3).strip()
                else:
                    m3 = re.search(
                        rf"[\(\-\–]\s*(?:qty:?\s*)?(\d+)\s*({unit_pattern})?\s*[\)]?$",
                        line,
                        re.IGNORECASE,
                    )
                    if m3:
                        qty = int(m3.group(1))
                        if m3.group(2):
                            unit = m3.group(2).lower()
                        name = line[: m3.start()].strip()
                        name = re.sub(r"[\(\-\–]+$", "", name).strip()

            name = name.strip()
            if name:
                items.append(
                    ItemCreate(
                        name=name,
                        quantity=qty,
                        unit=unit,
                        location_id=location_id,
                        status="stored",
                    )
                )
        return items

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

        created_items = [cls.create(db, it_data) for it_data in items_to_create]
        db.flush()
        return created_items

    @classmethod
    def update(cls, db: Session, item_id: int, data: ItemUpdate) -> Item:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")

        old_photo = item.item_photo
        update_dict = data.model_dump(exclude_unset=True)
        now = datetime.now(timezone.utc)

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
            photo_service.cleanup_unreferenced_photo(db, old_photo)

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
        db.delete(item)
        db.flush()
        if old_photo:
            photo_service.cleanup_unreferenced_photo(db, old_photo)

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
        db.commit()
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
        db.flush()
        if item.item_photo == old_path:
            item.item_photo = None
        db.commit()
        photo_service.delete_attachment_files(db, old_path, old_thumb)


inventory_service = InventoryService()
