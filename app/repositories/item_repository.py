from typing import List, Optional, Set
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from app.models.item import Item, ItemAttachment
from app.models.location import Location, LocationPhoto
from app.schemas.common import ItemSortOrder


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ItemRepository:
    @staticmethod
    def get_by_id(db: Session, item_id: int, include_audits: bool = False) -> Optional[Item]:
        stmt = select(Item).where(Item.id == item_id)
        if include_audits:
            stmt = stmt.options(selectinload(Item.audit_logs))
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_filtered(
        db: Session,
        category: Optional[str] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        sort: ItemSortOrder = ItemSortOrder.last_seen_desc,
        limit: int = 100,
    ) -> List[Item]:
        stmt = select(Item)
        if category:
            pat = f"%{escape_like(category.strip())}%"
            stmt = stmt.where(Item.category.ilike(pat, escape="\\"))
        if location_id is not None:
            stmt = stmt.where(Item.location_id == location_id)
        if status:
            stmt = stmt.where(Item.status == status)

        sort_map = {
            ItemSortOrder.last_seen_desc: (Item.last_seen_at.desc(), Item.id.desc()),
            ItemSortOrder.last_seen_asc: (Item.last_seen_at.asc(), Item.id.asc()),
            ItemSortOrder.name_asc: (func.lower(Item.name).asc(), Item.id.asc()),
            ItemSortOrder.name_desc: (func.lower(Item.name).desc(), Item.id.desc()),
            ItemSortOrder.qty_desc: (func.coalesce(Item.quantity, 0).desc(), Item.id.desc()),
            ItemSortOrder.updated_desc: (Item.updated_at.desc(), Item.id.desc()),
        }
        order_clauses = sort_map.get(sort, (Item.last_seen_at.desc(), Item.id.desc()))
        stmt = stmt.order_by(*order_clauses).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_by_location(db: Session, location_id: int) -> List[Item]:
        stmt = select(Item).where(Item.location_id == location_id).order_by(Item.last_seen_at.desc(), Item.id.desc())
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_recent(db: Session, limit: int = 100) -> List[Item]:
        stmt = select(Item).order_by(Item.updated_at.desc(), Item.id.desc()).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def search_items(
        db: Session,
        tokens: List[str],
        descendant_location_ids: Set[int],
        limit: int = 100,
    ) -> List[Item]:
        item_clauses = []
        for tok in tokens:
            pat = f"%{escape_like(tok)}%"
            item_clauses.append(
                or_(
                    Item.name.ilike(pat, escape="\\"),
                    Item.description.ilike(pat, escape="\\"),
                    Item.category.ilike(pat, escape="\\"),
                    Item.tags.ilike(pat, escape="\\"),
                    Item.serial_or_part_number.ilike(pat, escape="\\"),
                )
            )

        final_filter = or_(*item_clauses)
        if descendant_location_ids:
            final_filter = or_(final_filter, Item.location_id.in_(descendant_location_ids))

        stmt = select(Item).where(final_filter).order_by(Item.last_seen_at.desc(), Item.id.desc()).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def count_photo_references(db: Session, photo_url: str) -> int:
        if not photo_url:
            return 0
        item_refs = db.execute(
            select(func.count(Item.id)).where(Item.item_photo == photo_url)
        ).scalar() or 0
        loc_refs = db.execute(
            select(func.count(Location.id)).where(
                or_(Location.container_photo == photo_url, Location.context_photo == photo_url)
            )
        ).scalar() or 0
        loc_photo_refs = db.execute(
            select(func.count(LocationPhoto.id)).where(LocationPhoto.photo_path == photo_url)
        ).scalar() or 0
        att_refs = db.execute(
            select(func.count(ItemAttachment.id)).where(ItemAttachment.file_path == photo_url)
        ).scalar() or 0
        return int(item_refs) + int(loc_refs) + int(loc_photo_refs) + int(att_refs)


item_repository = ItemRepository()

