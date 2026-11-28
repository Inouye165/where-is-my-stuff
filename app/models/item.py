from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base, utc_now

if TYPE_CHECKING:
    from app.models.audit import AuditLog
    from app.models.location import Location


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

    item_photo: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)

    location_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(32), default="stored", nullable=False, index=True)
    status_note: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False, index=True
    )

    location: Mapped[Optional["Location"]] = relationship("Location", back_populates="items")
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="desc(AuditLog.recorded_at)",
    )
    attachments: Mapped[List["ItemAttachment"]] = relationship(
        "ItemAttachment",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="desc(ItemAttachment.created_at), desc(ItemAttachment.id)",
    )


class ItemAttachment(Base):
    __tablename__ = "item_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    attachment_type: Mapped[str] = mapped_column(String(32), nullable=False, default="document")
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    item: Mapped["Item"] = relationship("Item", back_populates="attachments")
