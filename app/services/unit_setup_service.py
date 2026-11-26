from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import secrets
from typing import List, Optional
from fastapi import status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from app.core.exceptions import AppError, BadRequestError, NotFoundError
from app.core.network import build_qr_png, resolve_effective_base_url
from app.models.location import Location
from app.models.session import UnitSetupSessionRecord
from app.repositories.location_repository import location_repository
from app.schemas.location import LocationPhotoRead, LocationRead
from app.schemas.unit_setup import (
    UnitSetupCreateUnitRequest,
    UnitSetupDrawerSummary,
    UnitSetupProgressUpdate,
    UnitSetupSessionStatusResponse,
)
from app.services.location_service import location_service


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _db_ts(db: Session, dt: datetime) -> datetime:
    utc_dt = _as_utc(dt)
    if db.get_bind().dialect.name == "sqlite":
        return utc_dt.replace(tzinfo=None)
    return utc_dt


@dataclass
class UnitSetupSession:
    token: str
    room_id: Optional[int]
    room_name: Optional[str]
    created_at: datetime
    expires_at: datetime
    status: str = "waiting_for_phone"
    unit_id: Optional[int] = None
    unit_name: Optional[str] = None
    current_drawer_index: int = 0
    unit_creating: bool = False


class UnitSetupService:
    """Database-backed coordinator for guided desktop <-> mobile storage unit setup sessions."""

    TTL_SECONDS = 1800
    MAX_SESSIONS = 500

    @staticmethod
    def _to_snapshot(rec: UnitSetupSessionRecord) -> UnitSetupSession:
        return UnitSetupSession(
            token=rec.token,
            room_id=rec.room_id,
            room_name=rec.room_name,
            created_at=_as_utc(rec.created_at),
            expires_at=_as_utc(rec.expires_at),
            status=rec.status,
            unit_id=rec.unit_id,
            unit_name=rec.unit_name,
            current_drawer_index=rec.current_drawer_index,
            unit_creating=bool(rec.unit_creating),
        )

    @staticmethod
    def _prune_expired(db: Session, now: datetime, include_recently_expired: bool = False) -> None:
        cutoff = now if include_recently_expired else (now - timedelta(minutes=30))
        db.execute(delete(UnitSetupSessionRecord).where(UnitSetupSessionRecord.expires_at < _db_ts(db, cutoff)))

    def create_session(
        self,
        db: Session,
        room_id: Optional[int] = None,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> dict:
        room_name: Optional[str] = None
        if room_id is not None:
            room = location_repository.get_by_id(db, room_id)
            if not room:
                raise NotFoundError(f"Room {room_id} does not exist.")
            room_name = room.name

        now = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(24)

        self._prune_expired(db, now)
        active_count = int(db.execute(select(func.count(UnitSetupSessionRecord.token))).scalar() or 0)
        if active_count >= self.MAX_SESSIONS:
            self._prune_expired(db, now, include_recently_expired=True)
            active_count = int(db.execute(select(func.count(UnitSetupSessionRecord.token))).scalar() or 0)
        if active_count >= self.MAX_SESSIONS:
            raise AppError(
                "Too many active unit setup sessions. Please wait and try again.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_code="too_many_sessions",
            )

        rec = UnitSetupSessionRecord(
            token=token,
            room_id=room_id,
            room_name=room_name,
            status="waiting_for_phone",
            current_drawer_index=0,
            unit_creating=False,
            created_at=_db_ts(db, now),
            expires_at=_db_ts(db, now + timedelta(seconds=self.TTL_SECONDS)),
        )
        db.add(rec)
        db.flush()

        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        mobile_url = f"{effective_base}/setup.html?token={token}"
        return {
            "token": token,
            "room_id": room_id,
            "room_name": room_name,
            "status": "waiting_for_phone",
            "expires_in_seconds": self.TTL_SECONDS,
            "mobile_url": mobile_url,
            "qr_url": f"/api/v1/unit-setup/sessions/{token}/qr",
        }

    def get_session(self, db: Session, token: str) -> UnitSetupSession:
        now = datetime.now(timezone.utc)
        rec = db.get(UnitSetupSessionRecord, token)
        if not rec:
            raise NotFoundError("Unit setup session not found.")
        if now > _as_utc(rec.expires_at) and rec.status != "completed":
            rec.status = "expired"
            db.flush()
        return self._to_snapshot(rec)

    def require_active_session(self, db: Session, token: str) -> UnitSetupSession:
        sess = self.get_session(db, token)
        if sess.status == "expired" or (
            sess.status != "completed" and datetime.now(timezone.utc) > sess.expires_at
        ):
            raise AppError(
                "Unit setup session has expired. Please generate a new QR code.",
                status_code=status.HTTP_410_GONE,
                error_code="session_expired",
            )
        return sess

    def _build_enriched_response(self, db: Session, sess: UnitSetupSession) -> UnitSetupSessionStatusResponse:
        unit_read: Optional[LocationRead] = None
        drawers: List[UnitSetupDrawerSummary] = []

        if sess.unit_id is not None:
            snapshot = location_service.build_scoped_snapshot(
                db, [sess.unit_id], include_counts=True, include_photos=True, photos_for_descendants=True
            )
            unit_loc = snapshot.loc_by_id.get(sess.unit_id)
            if unit_loc is not None:
                unit_read = snapshot.to_read(unit_loc)
                children = list(snapshot.children_by_parent.get(unit_loc.id, []))
                children.sort(
                    key=lambda ch: (
                        ch.grid_row if ch.grid_row is not None else 999,
                        ch.grid_col if ch.grid_col is not None else 999,
                        ch.id,
                    )
                )
                for ch in children:
                    photo_models = [
                        LocationPhotoRead.model_validate(p)
                        for p in snapshot.photos_by_location.get(ch.id, [])
                    ]
                    drawers.append(
                        UnitSetupDrawerSummary(
                            id=ch.id,
                            name=ch.name,
                            location_type=ch.location_type,
                            grid_row=ch.grid_row,
                            grid_col=ch.grid_col,
                            photo_count=len(photo_models),
                            item_count=snapshot.total_items_for(ch.id),
                            photos=photo_models,
                        )
                    )

        remaining_seconds = max(0, int((sess.expires_at - datetime.now(timezone.utc)).total_seconds()))
        return UnitSetupSessionStatusResponse(
            token=sess.token,
            status=sess.status,  # type: ignore[arg-type]
            room_id=sess.room_id,
            room_name=sess.room_name,
            unit_id=sess.unit_id,
            unit_name=unit_read.name if unit_read else sess.unit_name,
            current_drawer_index=sess.current_drawer_index,
            expires_in_seconds=remaining_seconds,
            unit=unit_read,
            drawers=drawers,
        )

    def get_session_status(self, db: Session, token: str) -> UnitSetupSessionStatusResponse:
        sess = self.get_session(db, token)
        return self._build_enriched_response(db, sess)

    def connect_phone(self, db: Session, token: str) -> UnitSetupSessionStatusResponse:
        self.require_active_session(db, token)
        now = datetime.now(timezone.utc)
        rec = db.get(UnitSetupSessionRecord, token)
        if not rec:
            raise NotFoundError("Unit setup session not found.")
        if rec.status == "waiting_for_phone":
            rec.status = "creating_unit"
        rec.expires_at = _db_ts(db, now + timedelta(seconds=self.TTL_SECONDS))
        db.flush()
        return self._build_enriched_response(db, self._to_snapshot(rec))

    def create_unit_for_session(
        self,
        db: Session,
        token: str,
        data: UnitSetupCreateUnitRequest,
    ) -> UnitSetupSessionStatusResponse:
        self.require_active_session(db, token)
        reserved = db.execute(
            update(UnitSetupSessionRecord)
            .where(
                UnitSetupSessionRecord.token == token,
                UnitSetupSessionRecord.unit_id.is_(None),
                UnitSetupSessionRecord.unit_creating.is_(False),
            )
            .values(unit_creating=True)
        )
        if reserved.rowcount != 1:
            raise BadRequestError("A storage unit has already been created for this setup session.")
        db.commit()

        try:
            return self._create_unit_reserved(db, token, data)
        except Exception:
            db.rollback()
            db.execute(
                update(UnitSetupSessionRecord)
                .where(
                    UnitSetupSessionRecord.token == token,
                    UnitSetupSessionRecord.unit_id.is_(None),
                )
                .values(unit_creating=False)
            )
            db.commit()
            raise

    def _create_unit_reserved(
        self,
        db: Session,
        token: str,
        data: UnitSetupCreateUnitRequest,
    ) -> UnitSetupSessionStatusResponse:
        rec = db.get(UnitSetupSessionRecord, token)
        if not rec:
            raise NotFoundError("Unit setup session not found.")
        room_id = rec.room_id
        if room_id is not None and location_repository.get_by_id(db, room_id) is None:
            raise NotFoundError(f"Room {room_id} no longer exists.")

        count = data.compartment_count
        cols = min(data.grid_cols, count) if count > 0 else None
        rows = math.ceil(count / cols) if (count > 0 and cols) else None
        clean_label = (data.label or "").strip()[:40] or None
        unit_loc_type = (
            "box"
            if count == 0 and (data.unit_kind or "").strip().lower() in ("box", "tote", "bin", "kit")
            else "container"
        )

        unit = Location(
            name=data.name.strip(),
            label=clean_label,
            description=data.description.strip() if data.description and data.description.strip() else None,
            location_type=unit_loc_type,
            parent_id=room_id,
            grid_rows=rows,
            grid_cols=cols,
        )
        db.add(unit)
        db.flush()

        if count > 0 and cols:
            label = data.compartment_label.strip() or "Drawer"
            comp_type = (
                data.compartment_type.value
                if hasattr(data.compartment_type, "value")
                else str(data.compartment_type)
            )
            for idx in range(count):
                r = (idx // cols) + 1
                c = (idx % cols) + 1
                child = Location(
                    name=f"{label} {idx + 1}",
                    location_type=comp_type,
                    parent_id=unit.id,
                    grid_row=r,
                    grid_col=c,
                    grid_row_span=1,
                    grid_col_span=1,
                )
                db.add(child)

        now = datetime.now(timezone.utc)
        rec.unit_id = unit.id
        rec.unit_name = unit.name
        rec.status = "capturing_drawers"
        rec.current_drawer_index = 0
        rec.unit_creating = False
        rec.expires_at = _db_ts(db, now + timedelta(seconds=self.TTL_SECONDS))
        db.commit()
        db.refresh(unit)

        return self._build_enriched_response(db, self._to_snapshot(rec))

    def update_progress(
        self,
        db: Session,
        token: str,
        data: UnitSetupProgressUpdate,
    ) -> UnitSetupSessionStatusResponse:
        self.require_active_session(db, token)
        now = datetime.now(timezone.utc)
        rec = db.get(UnitSetupSessionRecord, token)
        if not rec:
            raise NotFoundError("Unit setup session not found.")
        if data.current_drawer_index is not None:
            rec.current_drawer_index = data.current_drawer_index
        if data.status is not None:
            rec.status = data.status
        rec.expires_at = _db_ts(db, now + timedelta(seconds=self.TTL_SECONDS))
        db.flush()
        return self._build_enriched_response(db, self._to_snapshot(rec))

    def generate_qr_png(
        self,
        db: Session,
        token: str,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> bytes:
        self.require_active_session(db, token)
        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        mobile_url = f"{effective_base}/setup.html?token={token}"
        return build_qr_png(mobile_url)


unit_setup_service = UnitSetupService()
