from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base, utc_now


class CaptureSessionRecord(Base):
    __tablename__ = "capture_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    location_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    photos_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class UnitSetupSessionRecord(Base):
    __tablename__ = "unit_setup_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    room_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    room_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting_for_phone")
    unit_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    unit_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    current_drawer_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_creating: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
