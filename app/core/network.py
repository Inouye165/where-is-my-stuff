from io import BytesIO
import os
import re
import socket
from typing import Optional
from urllib.parse import urlsplit
import qrcode
from app.core.config import settings
from app.core.exceptions import BadRequestError

_SCHEME_PREFIX_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def validate_http_url(raw_url: str, *, allow_scheme_less: bool = False) -> str:
    """Validate that a URL is a clean http/https URL without credentials or control characters."""
    if not isinstance(raw_url, str):
        raise BadRequestError("Invalid URL.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_url):
        raise BadRequestError("Invalid URL.")
    val = raw_url.strip()
    if not val or any(ch.isspace() for ch in val):
        raise BadRequestError("Invalid URL.")
    if "://" not in val:
        if not allow_scheme_less or _SCHEME_PREFIX_RE.match(val):
            raise BadRequestError("URL must use http:// or https://.")
        val = f"http://{val}"

    try:
        parsed = urlsplit(val)
        port = parsed.port
    except ValueError as exc:
        raise BadRequestError("Invalid URL.") from exc

    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise BadRequestError("URL must use http:// or https:// with a valid host.")
    if parsed.username is not None or parsed.password is not None:
        raise BadRequestError("URL must not contain embedded credentials.")
    if port is not None and not (1 <= port <= 65535):
        raise BadRequestError("Invalid URL port.")
    return val.rstrip("/")

def get_lan_ip() -> str:
    """
    Detects the host machine's active local network IPv4 address.
    Honors optional PUBLIC_BASE_URL environment variable or setting.
    """
    public_override = os.environ.get("PUBLIC_BASE_URL") or settings.PUBLIC_BASE_URL
    if public_override:
        raw = public_override.strip()
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        if parsed.hostname:
            return parsed.hostname

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

def resolve_effective_base_url(host_header: Optional[str] = None, base_url_param: Optional[str] = None) -> str:
    """
    Determines the target base URL for QR codes and deep links.
    1. Explicit base_url_param query parameter (e.g. from Print QR modal input).
    2. PUBLIC_BASE_URL environment variable.
    3. Host header: if localhost/127.0.0.1/0.0.0.0/::1, replace with LAN IP and port.
    4. Fallback to http://<LAN_IP>:<PORT>.
    """
    if base_url_param and base_url_param.strip():
        return validate_http_url(base_url_param, allow_scheme_less=True)

    public_override = os.environ.get("PUBLIC_BASE_URL") or settings.PUBLIC_BASE_URL
    if public_override:
        return validate_http_url(public_override, allow_scheme_less=True)

    lan_ip = get_lan_ip()
    port = settings.PORT

    if not host_header:
        return f"http://{lan_ip}:{port}"

    clean_host = host_header.strip().replace("\r", "").replace("\n", "")
    parsed = urlsplit(f"//{clean_host}")
    req_host = (parsed.hostname or "").lower()
    try:
        parsed_port = parsed.port
    except ValueError:
        return f"http://{lan_ip}:{port}"
    req_port = str(parsed_port) if parsed_port else str(port)

    if req_host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return f"http://{lan_ip}:{req_port}"
    return f"http://{clean_host}"


def is_https_active() -> bool:
    """Return True when the local server process is actively serving LAN HTTPS."""
    return os.environ.get("WIMS_HTTPS_ACTIVE") == "1"


def _resolve_active_https_port() -> int:
    raw = (os.environ.get("WIMS_HTTPS_PORT") or "").strip()
    if raw:
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
    return settings.HTTPS_PORT


def resolve_mobile_camera_base_url(
    host_header: Optional[str] = None,
    base_url_param: Optional[str] = None,
) -> str:
    """
    Resolve base URL for mobile camera pages (/setup.html).
    Uses local HTTPS when active so mobile browsers allow getUserMedia() camera streams,
    unless overridden by an explicit base_url_param or PUBLIC_BASE_URL.
    """
    if base_url_param and base_url_param.strip():
        return resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)

    public_override = os.environ.get("PUBLIC_BASE_URL") or settings.PUBLIC_BASE_URL
    if public_override:
        return resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)

    if is_https_active():
        target_host = get_lan_ip()
        if host_header:
            clean_host = host_header.strip().replace("\r", "").replace("\n", "")
            parsed = urlsplit(f"//{clean_host}")
            req_host = (parsed.hostname or "").lower()
            if req_host and req_host not in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
                target_host = parsed.hostname or target_host
        host_part = f"[{target_host}]" if ":" in target_host and not target_host.startswith("[") else target_host
        return f"https://{host_part}:{_resolve_active_https_port()}"

    return resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)


def build_qr_png(target_url: str) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

