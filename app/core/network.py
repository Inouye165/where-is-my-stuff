from io import BytesIO
import os
import socket
from typing import Optional
from urllib.parse import urlsplit
import qrcode
from app.core.config import settings

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
        val = base_url_param.strip().replace("\r", "").replace("\n", "").rstrip("/")
        if not val.startswith("http://") and not val.startswith("https://"):
            val = f"http://{val}"
        return val

    public_override = os.environ.get("PUBLIC_BASE_URL") or settings.PUBLIC_BASE_URL
    if public_override:
        val = public_override.strip().replace("\r", "").replace("\n", "").rstrip("/")
        if not val.startswith("http://") and not val.startswith("https://"):
            val = f"http://{val}"
        return val

    lan_ip = get_lan_ip()
    port = settings.PORT

    if not host_header:
        return f"http://{lan_ip}:{port}"

    clean_host = host_header.strip().replace("\r", "").replace("\n", "")
    parsed = urlsplit(f"//{clean_host}")
    req_host = (parsed.hostname or "").lower()
    req_port = str(parsed.port) if parsed.port else str(port)

    if req_host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return f"http://{lan_ip}:{req_port}"
    return f"http://{clean_host}"


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

