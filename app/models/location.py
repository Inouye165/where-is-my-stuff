from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.item import Item


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location_type: Mapped[str] = mapped_column(String(50), nullable=False, default="room", index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    container_photo: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    context_photo: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    grid_rows: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_cols: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    grid_row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_col: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_row_span: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_col_span: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now, nullable=False)

    parent: Mapped[Optional["Location"]] = relationship(
        "Location", remote_side=[id], back_populates="children"
    )
    children: Mapped[List["Location"]] = relationship(
        "Location", back_populates="parent", cascade="all, delete-orphan"
    )
    items: Mapped[List["Item"]] = relationship("Item", back_populates="location")

    def get_breadcrumbs(self) -> List[dict[str, Any]]:
        crumbs: List[dict[str, Any]] = []
        curr: Optional[Location] = self
        visited: set[int] = set()
        while curr and curr.id not in visited:
            visited.add(curr.id)
            crumbs.insert(
                0,
                {
                    "id": curr.id,
                    "name": curr.name,
                    "location_type": curr.location_type,
                },
            )
            curr = curr.parent
        return crumbs

    @property
    def full_path(self) -> str:
        return " > ".join(c["name"] for c in self.get_breadcrumbs())
