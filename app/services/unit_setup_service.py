from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import secrets
from threading import Lock
from typing import Dict, List, Optional
from fastapi import status
from sqlalchemy.orm import Session
from app.core.exceptions import AppError, BadRequestError, NotFoundError
from app.core.network import build_qr_png, resolve_effective_base_url
from app.models.location import Location
from app.repositories.location_repository import location_repository
from app.schemas.location import LocationPhotoRead, LocationRead
from app.schemas.unit_setup import (
    UnitSetupCreateUnitRequest,
    UnitSetupDrawerSummary,
    UnitSetupProgressUpdate,
    UnitSetupSessionStatusResponse,
)
from app.services.location_service import location_service


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
    """In-memory coordinator for guided desktop <-> mobile storage unit setup sessions."""

    TTL_SECONDS = 1800

    def __init__(self) -> None:
        self._sessions: Dict[str, UnitSetupSession] = {}
        self._lock = Lock()

    def _prune_expired(self, now: datetime) -> None:
        stale_cutoff = now - timedelta(minutes=30)
        expired_tokens = [
            tok for tok, sess in self._sessions.items()
            if sess.expires_at < stale_cutoff
        ]
        for tok in expired_tokens:
            del self._sessions[tok]

    @staticmethod
    def _snapshot(sess: UnitSetupSession) -> UnitSetupSession:
        return UnitSetupSession(
            token=sess.token,
            room_id=sess.room_id,
            room_name=sess.room_name,
            created_at=sess.created_at,
            expires_at=sess.expires_at,
            status=sess.status,
            unit_id=sess.unit_id,
            unit_name=sess.unit_name,
            current_drawer_index=sess.current_drawer_index,
        )

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

        with self._lock:
            self._prune_expired(now)
            self._sessions[token] = UnitSetupSession(
                token=token,
                room_id=room_id,
                room_name=room_name,
                created_at=now,
                expires_at=now + timedelta(seconds=self.TTL_SECONDS),
            )

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

    def get_session(self, token: str) -> UnitSetupSession:
        now = datetime.now(timezone.utc)
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                raise NotFoundError("Unit setup session not found.")
            if now > sess.expires_at and sess.status != "completed":
                sess.status = "expired"
            return self._snapshot(sess)

    def require_active_session(self, token: str) -> UnitSetupSession:
        sess = self.get_session(token)
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
            snapshot = location_service.build_snapshot(db, include_counts=True, include_photos=True)
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
        sess = self.get_session(token)
        return self._build_enriched_response(db, sess)

    def connect_phone(self, db: Session, token: str) -> UnitSetupSessionStatusResponse:
        self.require_active_session(token)
        now = datetime.now(timezone.utc)
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                raise NotFoundError("Unit setup session not found.")
            if sess.status == "waiting_for_phone":
                sess.status = "creating_unit"
            sess.expires_at = now + timedelta(seconds=self.TTL_SECONDS)
            snap = self._snapshot(sess)
        return self._build_enriched_response(db, snap)

    def create_unit_for_session(
        self,
        db: Session,
        token: str,
        data: UnitSetupCreateUnitRequest,
    ) -> UnitSetupSessionStatusResponse:
        self.require_active_session(token)
        with self._lock:
            reserved = self._sessions.get(token)
            if reserved is None:
                raise NotFoundError("Unit setup session not found.")
            if reserved.unit_id is not None or reserved.unit_creating:
                raise BadRequestError("A storage unit has already been created for this setup session.")
            reserved.unit_creating = True
            room_id = reserved.room_id

        try:
            return self._create_unit_reserved(db, token, data, room_id)
        finally:
            with self._lock:
                live = self._sessions.get(token)
                if live is not None:
                    live.unit_creating = False

    def _create_unit_reserved(
        self,
        db: Session,
        token: str,
        data: UnitSetupCreateUnitRequest,
        room_id: Optional[int],
    ) -> UnitSetupSessionStatusResponse:
        sess = self.get_session(token)
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
            parent_id=sess.room_id,
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

        db.commit()
        db.refresh(unit)

        now = datetime.now(timezone.utc)
        with self._lock:
            live = self._sessions.get(token)
            if not live:
                raise NotFoundError("Unit setup session not found.")
            live.unit_id = unit.id
            live.unit_name = unit.name
            live.status = "capturing_drawers"
            live.current_drawer_index = 0
            live.expires_at = now + timedelta(seconds=self.TTL_SECONDS)
            snap = self._snapshot(live)

        return self._build_enriched_response(db, snap)

    def update_progress(
        self,
        db: Session,
        token: str,
        data: UnitSetupProgressUpdate,
    ) -> UnitSetupSessionStatusResponse:
        self.require_active_session(token)
        now = datetime.now(timezone.utc)
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                raise NotFoundError("Unit setup session not found.")
            if data.current_drawer_index is not None:
                sess.current_drawer_index = data.current_drawer_index
            if data.status is not None:
                sess.status = data.status
            sess.expires_at = now + timedelta(seconds=self.TTL_SECONDS)
            snap = self._snapshot(sess)
        return self._build_enriched_response(db, snap)

    def generate_qr_png(
        self,
        token: str,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> bytes:
        self.require_active_session(token)
        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        mobile_url = f"{effective_base}/setup.html?token={token}"
        return build_qr_png(mobile_url)


unit_setup_service = UnitSetupService()
