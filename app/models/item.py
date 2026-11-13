from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.audit import AuditLog
    from app.models.location import Location


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    tags: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, default="pcs")
    serial_or_part_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)

    item_photo: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    location_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(32), default="stored", nullable=False, index=True)
    status_note: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, onupdate=_utc_now, nullable=False, index=True
    )

    location: Mapped[Optional["Location"]] = relationship("Location", back_populates="items")
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="desc(AuditLog.recorded_at)",
    )
