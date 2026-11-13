from typing import Dict, Iterable, List, Optional, Set
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.models.item import Item
from app.models.location import Location


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class LocationRepository:
    @staticmethod
    def get_by_id(db: Session, location_id: int) -> Optional[Location]:
        stmt = select(Location).where(Location.id == location_id)
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_all_ordered(db: Session) -> List[Location]:
        stmt = select(Location).order_by(Location.id.asc())
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def get_direct_item_counts(db: Session) -> Dict[int, int]:
        stmt = (
            select(Item.location_id, func.count(Item.id))
            .where(Item.location_id.is_not(None))
            .group_by(Item.location_id)
        )
        return {int(loc_id): int(cnt) for loc_id, cnt in db.execute(stmt).all() if loc_id is not None}

    @staticmethod
    def get_descendant_ids(db: Session, seed_ids: Iterable[int]) -> Set[int]:
        seeds = [int(i) for i in seed_ids]
        if not seeds:
            return set()

        base_cte = (
            select(Location.id)
            .where(Location.id.in_(seeds))
            .cte(name="location_descendants", recursive=True)
        )
        recursive_part = select(Location.id).join(base_cte, Location.parent_id == base_cte.c.id)
        cte = base_cte.union(recursive_part)
        rows = db.execute(select(cte.c.id)).scalars().all()
        return {int(r) for r in rows}

    @classmethod
    def search_matching_and_descendant_ids(cls, db: Session, tokens: List[str]) -> Set[int]:
        if not tokens:
            return set()
        clauses = []
        for tok in tokens:
            pat = f"%{_escape_like(tok)}%"
            clauses.append(Location.name.ilike(pat, escape="\\"))

        matched = db.execute(select(Location.id).where(or_(*clauses))).scalars().all()
        if not matched:
            return set()
        return cls.get_descendant_ids(db, matched)

    @classmethod
    def collect_subtree_photos(cls, db: Session, root_location_id: int) -> Set[str]:
        subtree_ids = cls.get_descendant_ids(db, [root_location_id])
        if not subtree_ids:
            return set()
        stmt = select(Location.container_photo, Location.context_photo).where(Location.id.in_(subtree_ids))
        photos: Set[str] = set()
        for c_photo, ctx_photo in db.execute(stmt).all():
            if c_photo:
                photos.add(c_photo)
            if ctx_photo:
                photos.add(ctx_photo)
        return photos


location_repository = LocationRepository()

