from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import List, Optional
from fastapi import UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from app.core.exceptions import AppError, NotFoundError
from app.core.network import build_qr_png, resolve_effective_base_url
from app.models.session import CaptureSessionRecord
from app.repositories.location_repository import location_repository
from app.services.location_service import location_service
from app.services.photo_service import photo_service


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _db_ts(db: Session, dt: datetime) -> datetime:
    utc_dt = _as_utc(dt)
    if db.get_bind().dialect.name == "sqlite":
        return utc_dt.replace(tzinfo=None)
    return utc_dt


@dataclass
class CaptureSession:
    token: str
    label: str
    created_at: datetime
    expires_at: datetime
    location_id: Optional[int] = None
    status: str = "pending"
    photos: List[dict] = field(default_factory=list)


class CaptureSessionService:
    """Database-backed store for short-lived mobile camera QR handoff sessions."""

    TTL_SECONDS = 600
    MAX_SESSIONS = 500

    @staticmethod
    def _to_snapshot(rec: CaptureSessionRecord) -> CaptureSession:
        try:
            raw_photos = json.loads(rec.photos_json or "[]")
            photos = [dict(p) for p in raw_photos if isinstance(p, dict)]
        except (ValueError, TypeError):
            photos = []
        return CaptureSession(
            token=rec.token,
            label=rec.label,
            created_at=_as_utc(rec.created_at),
            expires_at=_as_utc(rec.expires_at),
            location_id=rec.location_id,
            status=rec.status,
            photos=photos,
        )

    @staticmethod
    def _prune_expired(db: Session, now: datetime, include_recently_expired: bool = False) -> None:
        cutoff = now if include_recently_expired else (now - timedelta(minutes=30))
        db.execute(delete(CaptureSessionRecord).where(CaptureSessionRecord.expires_at < _db_ts(db, cutoff)))

    def create_session(
        self,
        db: Session,
        label: Optional[str] = None,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
        location_id: Optional[int] = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(24)
        clean_label = (label or "Photo").strip()[:80] or "Photo"

        self._prune_expired(db, now)
        active_count = int(db.execute(select(func.count(CaptureSessionRecord.token))).scalar() or 0)
        if active_count >= self.MAX_SESSIONS:
            self._prune_expired(db, now, include_recently_expired=True)
            active_count = int(db.execute(select(func.count(CaptureSessionRecord.token))).scalar() or 0)
        if active_count >= self.MAX_SESSIONS:
            raise AppError(
                "Too many active capture sessions. Please wait and try again.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_code="too_many_sessions",
            )

        rec = CaptureSessionRecord(
            token=token,
            label=clean_label,
            location_id=location_id,
            status="pending",
            photos_json="[]",
            created_at=_db_ts(db, now),
            expires_at=_db_ts(db, now + timedelta(seconds=self.TTL_SECONDS)),
        )
        db.add(rec)
        db.flush()

        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        capture_url = f"{effective_base}/capture.html?token={token}"
        return {
            "token": token,
            "label": clean_label,
            "location_id": location_id,
            "expires_in_seconds": self.TTL_SECONDS,
            "capture_url": capture_url,
            "qr_url": f"/api/v1/photos/capture-sessions/{token}/qr",
        }

    def get_session(self, db: Session, token: str) -> CaptureSession:
        rec = db.get(CaptureSessionRecord, token)
        if not rec:
            raise NotFoundError("Capture session not found.")
        if datetime.now(timezone.utc) > _as_utc(rec.expires_at) and rec.status == "pending":
            rec.status = "expired"
            db.flush()
        return self._to_snapshot(rec)

    def require_active_session(self, db: Session, token: str) -> CaptureSession:
        sess = self.get_session(db, token)
        if sess.status == "expired" or datetime.now(timezone.utc) > sess.expires_at:
            raise AppError(
                "Capture session has expired. Please scan a new QR code.",
                status_code=status.HTTP_410_GONE,
                error_code="session_expired",
            )
        return sess

    def mark_uploaded(self, db: Session, token: str, photo_result: dict) -> CaptureSession:
        rec = db.get(CaptureSessionRecord, token)
        if not rec:
            raise NotFoundError("Capture session not found.")
        if datetime.now(timezone.utc) > _as_utc(rec.expires_at):
            rec.status = "expired"
            db.flush()
            raise AppError(
                "Capture session has expired. Please scan a new QR code.",
                status_code=status.HTTP_410_GONE,
                error_code="session_expired",
            )
        snap = self._to_snapshot(rec)
        photos = list(snap.photos)
        photos.append(dict(photo_result))
        rec.photos_json = json.dumps(photos)
        rec.status = "uploaded"
        db.flush()
        return self._to_snapshot(rec)

    def process_upload(self, db: Session, token: str, file: UploadFile) -> dict:
        sess = self.require_active_session(db, token)
        result = photo_service.process_and_save(file)
        photo_service.schedule_rollback_cleanup(db, result["original_path"], result.get("thumbnail_path"))

        attached_photo_id: Optional[int] = None
        if sess.location_id is not None and location_repository.get_by_id(db, sess.location_id) is not None:
            attached = location_service.attach_photo(
                db=db,
                location_id=sess.location_id,
                photo_path=result["original_path"],
                thumbnail_path=result.get("thumbnail_path"),
                photo_type="container",
            )
            attached_photo_id = attached.id
            db.commit()

        try:
            self.mark_uploaded(db, token, result)
        except Exception:
            if attached_photo_id is not None and sess.location_id is not None:
                location_service.delete_photo(db, sess.location_id, attached_photo_id)
                db.commit()
            else:
                photo_service.delete_upload_url(result["original_path"])
                photo_service.delete_upload_url(result.get("thumbnail_path"))
            raise

        return result

    def generate_qr_png(
        self,
        db: Session,
        token: str,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> bytes:
        self.require_active_session(db, token)
        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        capture_url = f"{effective_base}/capture.html?token={token}"
        return build_qr_png(capture_url)


capture_session_service = CaptureSessionService()
