import base64
from datetime import datetime, timezone
import json
from typing import Any, List, Optional, Set, Tuple
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload
from app.core.exceptions import BadRequestError
from app.models.item import Item, ItemAttachment
from app.models.location import Location, LocationPhoto
from app.schemas.common import ItemSortOrder


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_SORT_MAP = {
    ItemSortOrder.last_seen_desc: (Item.last_seen_at.desc(), Item.id.desc()),
    ItemSortOrder.last_seen_asc: (Item.last_seen_at.asc(), Item.id.asc()),
    ItemSortOrder.name_asc: (func.lower(Item.name).asc(), Item.id.asc()),
    ItemSortOrder.name_desc: (func.lower(Item.name).desc(), Item.id.desc()),
    ItemSortOrder.qty_desc: (func.coalesce(Item.quantity, 0).desc(), Item.id.desc()),
    ItemSortOrder.updated_desc: (Item.updated_at.desc(), Item.id.desc()),
}


def _as_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def encode_item_cursor(sort: ItemSortOrder, item: Item) -> str:
    if sort in (ItemSortOrder.last_seen_desc, ItemSortOrder.last_seen_asc):
        val: Any = _as_utc_iso(item.last_seen_at)
    elif sort == ItemSortOrder.updated_desc:
        val = _as_utc_iso(item.updated_at)
    elif sort in (ItemSortOrder.name_asc, ItemSortOrder.name_desc):
        val = (item.name or "").lower()
    elif sort == ItemSortOrder.qty_desc:
        val = int(item.quantity or 0)
    else:
        val = _as_utc_iso(item.last_seen_at)
    raw = json.dumps({"s": sort.value, "v": val, "id": int(item.id)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_item_cursor(cursor: str, expected_sort: ItemSortOrder) -> Tuple[Any, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("s") != expected_sort.value:
            raise ValueError("Cursor sort mismatch")
        item_id = int(payload["id"])
        raw_val = payload["v"]
        if expected_sort in (
            ItemSortOrder.last_seen_desc,
            ItemSortOrder.last_seen_asc,
            ItemSortOrder.updated_desc,
        ):
            dt = datetime.fromisoformat(str(raw_val))
            val: Any = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        elif expected_sort in (ItemSortOrder.name_asc, ItemSortOrder.name_desc):
            if not isinstance(raw_val, str):
                raise ValueError("Invalid string cursor value")
            val = raw_val
        elif expected_sort == ItemSortOrder.qty_desc:
            val = int(raw_val)
        else:
            raise ValueError("Unsupported sort")
        return val, item_id
    except Exception as exc:
        raise BadRequestError("Invalid pagination cursor.") from exc


def _apply_item_filters(
    stmt: Any,
    category: Optional[str] = None,
    location_id: Optional[int] = None,
    status: Optional[str] = None,
) -> Any:
    if category:
        pat = f"%{escape_like(category.strip())}%"
        stmt = stmt.where(Item.category.ilike(pat, escape="\\"))
    if location_id is not None:
        stmt = stmt.where(Item.location_id == location_id)
    if status:
        stmt = stmt.where(Item.status == status)
    return stmt


class ItemRepository:
    @staticmethod
    def get_by_id(db: Session, item_id: int, include_audits: bool = False) -> Optional[Item]:
        stmt = select(Item).where(Item.id == item_id)
        if include_audits:
            stmt = stmt.options(selectinload(Item.audit_logs), selectinload(Item.attachments))
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_filtered(
        db: Session,
        category: Optional[str] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        sort: ItemSortOrder = ItemSortOrder.last_seen_desc,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Item]:
        stmt = _apply_item_filters(select(Item), category=category, location_id=location_id, status=status)
        order_clauses = _SORT_MAP.get(sort, (Item.last_seen_at.desc(), Item.id.desc()))
        stmt = stmt.order_by(*order_clauses).offset(max(0, offset)).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_page(
        db: Session,
        category: Optional[str] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        sort: ItemSortOrder = ItemSortOrder.last_seen_desc,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Item], int, Optional[str], bool]:
        count_stmt = _apply_item_filters(
            select(func.count(Item.id)), category=category, location_id=location_id, status=status
        )
        total = int(db.execute(count_stmt).scalar() or 0)

        stmt = _apply_item_filters(select(Item), category=category, location_id=location_id, status=status)
        if cursor:
            val, cid = decode_item_cursor(cursor, sort)
            if isinstance(val, datetime) and db.get_bind().dialect.name == "sqlite":
                val = val.astimezone(timezone.utc).replace(tzinfo=None)

            if sort == ItemSortOrder.last_seen_desc:
                stmt = stmt.where(or_(Item.last_seen_at < val, and_(Item.last_seen_at == val, Item.id < cid)))
            elif sort == ItemSortOrder.last_seen_asc:
                stmt = stmt.where(or_(Item.last_seen_at > val, and_(Item.last_seen_at == val, Item.id > cid)))
            elif sort == ItemSortOrder.updated_desc:
                stmt = stmt.where(or_(Item.updated_at < val, and_(Item.updated_at == val, Item.id < cid)))
            elif sort == ItemSortOrder.name_asc:
                lname = func.lower(Item.name)
                stmt = stmt.where(or_(lname > val, and_(lname == val, Item.id > cid)))
            elif sort == ItemSortOrder.name_desc:
                lname = func.lower(Item.name)
                stmt = stmt.where(or_(lname < val, and_(lname == val, Item.id < cid)))
            elif sort == ItemSortOrder.qty_desc:
                qty_expr = func.coalesce(Item.quantity, 0)
                stmt = stmt.where(or_(qty_expr < val, and_(qty_expr == val, Item.id < cid)))

        order_clauses = _SORT_MAP.get(sort, (Item.last_seen_at.desc(), Item.id.desc()))
        rows = list(db.execute(stmt.order_by(*order_clauses).limit(limit + 1)).scalars().all())
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = encode_item_cursor(sort, page_rows[-1]) if (has_more and page_rows) else None
        return page_rows, total, next_cursor, has_more

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

