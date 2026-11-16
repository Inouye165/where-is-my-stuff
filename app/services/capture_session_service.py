from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import secrets
from threading import Lock
from typing import Dict, List, Optional
from fastapi import UploadFile, status
from sqlalchemy.orm import Session
from app.core.exceptions import AppError, NotFoundError
from app.core.network import build_qr_png, resolve_effective_base_url
from app.repositories.location_repository import location_repository
from app.services.location_service import location_service
from app.services.photo_service import photo_service


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
    """In-memory store for short-lived mobile camera QR handoff sessions."""

    TTL_SECONDS = 600

    def __init__(self) -> None:
        self._sessions: Dict[str, CaptureSession] = {}
        self._lock = Lock()

    def _prune_expired(self, now: datetime) -> None:
        stale_cutoff = now - timedelta(minutes=30)
        expired_tokens = [
            tok for tok, sess in self._sessions.items()
            if sess.expires_at < stale_cutoff
        ]
        for tok in expired_tokens:
            del self._sessions[tok]

    def create_session(
        self,
        label: Optional[str] = None,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
        location_id: Optional[int] = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(24)
        clean_label = (label or "Photo").strip()[:80] or "Photo"

        with self._lock:
            self._prune_expired(now)
            self._sessions[token] = CaptureSession(
                token=token,
                label=clean_label,
                created_at=now,
                expires_at=now + timedelta(seconds=self.TTL_SECONDS),
                location_id=location_id,
            )

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

    @staticmethod
    def _snapshot(sess: CaptureSession) -> CaptureSession:
        return CaptureSession(
            token=sess.token,
            label=sess.label,
            created_at=sess.created_at,
            expires_at=sess.expires_at,
            location_id=sess.location_id,
            status=sess.status,
            photos=[dict(p) for p in sess.photos],
        )

    def get_session(self, token: str) -> CaptureSession:
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                raise NotFoundError("Capture session not found.")
            if datetime.now(timezone.utc) > sess.expires_at and sess.status == "pending":
                sess.status = "expired"
            return self._snapshot(sess)

    def require_active_session(self, token: str) -> CaptureSession:
        sess = self.get_session(token)
        if sess.status == "expired" or datetime.now(timezone.utc) > sess.expires_at:
            raise AppError(
                "Capture session has expired. Please scan a new QR code.",
                status_code=status.HTTP_410_GONE,
                error_code="session_expired",
            )
        return sess

    def mark_uploaded(self, token: str, photo_result: dict) -> CaptureSession:
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                raise NotFoundError("Capture session not found.")
            if datetime.now(timezone.utc) > sess.expires_at:
                sess.status = "expired"
                raise AppError(
                    "Capture session has expired. Please scan a new QR code.",
                    status_code=status.HTTP_410_GONE,
                    error_code="session_expired",
                )
            sess.status = "uploaded"
            sess.photos.append(dict(photo_result))
            return self._snapshot(sess)

    def process_upload(self, db: Session, token: str, file: UploadFile) -> dict:
        sess = self.require_active_session(token)
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
            self.mark_uploaded(token, result)
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
        token: str,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> bytes:
        self.require_active_session(token)
        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        capture_url = f"{effective_base}/capture.html?token={token}"
        return build_qr_png(capture_url)


capture_session_service = CaptureSessionService()

