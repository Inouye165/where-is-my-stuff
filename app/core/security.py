import hashlib
import hmac
import re
import secrets
import time
from typing import Optional
from starlette.datastructures import Headers
from starlette.responses import Response
from app.core.config import settings

SESSION_COOKIE_NAME = "wims_session"
_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "localhost"}
_FALLBACK_PROCESS_SECRET = secrets.token_bytes(32)

_CAPTURE_SESSION_PATH_RE = re.compile(
    r"^/api/v1/photos/capture-sessions/[A-Za-z0-9_\-]+(?:/(?:qr|upload))?$"
)
_UNIT_SETUP_SESSION_PATH_RE = re.compile(
    r"^/api/v1/unit-setup/sessions/[A-Za-z0-9_\-]+(?:/(?:qr|connect|unit|progress))?$"
)
_LABEL_SCAN_SESSION_PATH_RE = re.compile(
    r"^/api/v1/label-scan/sessions/[A-Za-z0-9_\-]+(?:/(?:qr|upload))?$"
)
_PUBLIC_API_PATHS = {
    "/api/v1/health",
    "/api/v1/auth/status",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
}


def is_auth_enabled() -> bool:
    return bool((settings.WIMS_ACCESS_PIN or "").strip())


def verify_pin(candidate: Optional[str]) -> bool:
    pin = (settings.WIMS_ACCESS_PIN or "").strip()
    if not pin or not candidate:
        return False
    return secrets.compare_digest(candidate.strip(), pin)


def _signing_key() -> bytes:
    pin = (settings.WIMS_ACCESS_PIN or "").strip()
    base = (settings.WIMS_SESSION_SECRET or "").encode("utf-8") or _FALLBACK_PROCESS_SECRET
    # Mixing the active PIN into the HMAC key invalidates existing cookies immediately on PIN rotation.
    return hashlib.sha256(base + b":" + pin.encode("utf-8")).digest()


def create_session_token(ttl_seconds: Optional[int] = None) -> str:
    ttl = max(60, int(ttl_seconds if ttl_seconds is not None else settings.WIMS_SESSION_TTL_SECONDS))
    exp = int(time.time()) + ttl
    nonce = secrets.token_hex(12)
    payload = f"{exp}.{nonce}"
    sig = hmac.new(_signing_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: Optional[str]) -> bool:
    if not token or not is_auth_enabled():
        return False
    parts = token.strip().split(".")
    if len(parts) != 3:
        return False
    exp_str, nonce, sig = parts
    if not exp_str.isdigit() or not nonce or not sig:
        return False
    if int(exp_str) <= int(time.time()):
        return False
    expected = hmac.new(_signing_key(), f"{exp_str}.{nonce}".encode("ascii"), hashlib.sha256).hexdigest()
    return secrets.compare_digest(sig, expected)


def is_localhost_client(client_host: Optional[str]) -> bool:
    if not client_host:
        return False
    return client_host.strip().lower() in _LOCALHOST_HOSTS


def extract_cookie(cookie_header: str, name: str) -> Optional[str]:
    if not cookie_header:
        return None
    prefix = f"{name}="
    for part in cookie_header.split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix):].strip()
    return None


def is_request_authenticated(headers: Headers, client_host: Optional[str]) -> bool:
    if not is_auth_enabled():
        return True
    if settings.WIMS_AUTH_BYPASS_LOCALHOST and is_localhost_client(client_host):
        return True

    auth_header = (headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        if verify_pin(auth_header[7:]):
            return True

    x_token = headers.get("x-wims-token")
    if x_token and verify_pin(x_token):
        return True

    cookie_val = extract_cookie(headers.get("cookie") or "", SESSION_COOKIE_NAME)
    return verify_session_token(cookie_val)


def is_public_or_qr_scoped_path(method: str, path: str) -> bool:
    if method.upper() == "OPTIONS":
        return True
    if not path.startswith("/api/") and not path.startswith("/uploads/"):
        return True
    if path in _PUBLIC_API_PATHS:
        return True
    if _CAPTURE_SESSION_PATH_RE.match(path):
        return True
    if _UNIT_SETUP_SESSION_PATH_RE.match(path):
        return True
    if _LABEL_SCAN_SESSION_PATH_RE.match(path):
        return True
    return False


def set_session_cookie(
    response: Response,
    *,
    is_https: bool,
    ttl_seconds: Optional[int] = None,
) -> None:
    ttl = max(60, int(ttl_seconds if ttl_seconds is not None else settings.WIMS_SESSION_TTL_SECONDS))
    token = create_session_token(ttl_seconds=ttl)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
