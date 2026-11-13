import os
import socket
from typing import Optional
from app.core.config import settings

def get_lan_ip() -> str:
    """
    Detects the host machine's active local network IPv4 address.
    Honors optional PUBLIC_BASE_URL environment variable.
    """
    public_override = os.environ.get("PUBLIC_BASE_URL")
    if public_override:
        clean = public_override.strip().replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
        if clean:
            return clean

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def resolve_effective_base_url(host_header: Optional[str] = None, base_url_param: Optional[str] = None) -> str:
    """
    Determines the target base URL for QR codes and deep links.
    1. Explicit base_url_param query parameter (e.g. from Print QR modal input).
    2. PUBLIC_BASE_URL environment variable.
    3. Host header: if localhost/127.0.0.1/0.0.0.0, replace with LAN IP and port.
    4. Fallback to http://<LAN_IP>:<PORT>.
    """
    if base_url_param and base_url_param.strip():
        val = base_url_param.strip().replace("\r", "").replace("\n", "").rstrip("/")
        if not val.startswith("http://") and not val.startswith("https://"):
            val = f"http://{val}"
        return val

    public_override = os.environ.get("PUBLIC_BASE_URL")
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
    host_parts = clean_host.split(":")
    req_host = host_parts[0].lower()
    req_port = host_parts[1] if len(host_parts) > 1 else str(port)

    if req_host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return f"http://{lan_ip}:{req_port}"
    else:
        return f"http://{clean_host}"

