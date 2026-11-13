from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
import secrets
from threading import Lock
from typing import Dict, Optional
from fastapi import status
import qrcode
from app.core.exceptions import AppError, NotFoundError
from app.core.network import resolve_effective_base_url


@dataclass
class CaptureSession:
    token: str
    label: str
    created_at: datetime
    expires_at: datetime
    status: str = "pending"
    photo: Optional[dict] = None


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
            )

        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        capture_url = f"{effective_base}/capture.html?token={token}"
        return {
            "token": token,
            "label": clean_label,
            "expires_in_seconds": self.TTL_SECONDS,
            "capture_url": capture_url,
            "qr_url": f"/api/v1/photos/capture-sessions/{token}/qr",
        }

    def get_session(self, token: str) -> CaptureSession:
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                raise NotFoundError("Capture session not found.")
            if datetime.now(timezone.utc) > sess.expires_at and sess.status == "pending":
                sess.status = "expired"
            return sess

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
            sess.photo = photo_result
            return sess

    def generate_qr_png(
        self,
        token: str,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> bytes:
        self.require_active_session(token)
        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        capture_url = f"{effective_base}/capture.html?token={token}"

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(capture_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


capture_session_service = CaptureSessionService()

