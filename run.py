import asyncio
import os
import socket
from typing import Optional
import uvicorn
from app.core.config import settings
from app.core.tls import ensure_lan_tls_cert
from app.main import get_local_ip


def _pick_available_https_port(host: str, preferred_port: int, exclude_port: int) -> Optional[int]:
    """Find the first bindable TCP port starting from preferred_port (skipping exclude_port)."""
    for candidate in range(preferred_port, min(65536, preferred_port + 16)):
        if candidate == exclude_port:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return None


async def _serve_dual(http_server: uvicorn.Server, https_server: uvicorn.Server) -> None:
    async def _run_https_safely() -> None:
        try:
            await https_server.serve()
        except (Exception, SystemExit) as exc:
            if not http_server.should_exit:
                print(f" ! Warning: HTTPS server unavailable ({exc}); continuing on HTTP only.")
                os.environ.pop("WIMS_HTTPS_ACTIVE", None)
                os.environ.pop("WIMS_HTTPS_PORT", None)

    http_task = asyncio.create_task(http_server.serve())
    https_task = asyncio.create_task(_run_https_safely())
    done, pending = await asyncio.wait(
        {http_task, https_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if http_task in done:
        https_server.should_exit = True
    if https_task in pending:
        await https_task
    if http_task in pending:
        await http_task


if __name__ == "__main__":
    local_ip = get_local_ip()
    port = settings.PORT
    preferred_https_port = settings.HTTPS_PORT

    cert_pair = None
    active_https_port: Optional[int] = None
    if settings.HTTPS_ENABLED:
        active_https_port = _pick_available_https_port(settings.HOST, preferred_https_port, port)
        if active_https_port is not None:
            try:
                cert_pair = ensure_lan_tls_cert(settings.CERTS_DIR, local_ip)
                os.environ["WIMS_HTTPS_ACTIVE"] = "1"
                os.environ["WIMS_HTTPS_PORT"] = str(active_https_port)
            except Exception as exc:
                os.environ.pop("WIMS_HTTPS_ACTIVE", None)
                os.environ.pop("WIMS_HTTPS_PORT", None)
                active_https_port = None
                print(f" ! Warning: Could not prepare local HTTPS certificate ({exc}); running HTTP only.")
        else:
            os.environ.pop("WIMS_HTTPS_ACTIVE", None)
            os.environ.pop("WIMS_HTTPS_PORT", None)
            print(f" ! Warning: No free HTTPS port near {preferred_https_port}; running HTTP only.")
    else:
        os.environ.pop("WIMS_HTTPS_ACTIVE", None)
        os.environ.pop("WIMS_HTTPS_PORT", None)

    pin_status = "ENABLED (PIN required on LAN)" if (settings.WIMS_ACCESS_PIN or "").strip() else "DISABLED (open on local LAN)"
    print("=" * 65)
    print(f" {settings.APP_NAME}")
    print("=" * 65)
    print(f" * Localhost access:        http://localhost:{port}")
    print(f" * Local Network (HTTP):    http://{local_ip}:{port}")
    if cert_pair is not None and active_https_port is not None:
        fallback_note = f" (port {preferred_https_port} was busy)" if active_https_port != preferred_https_port else ""
        print(f" * Mobile Camera (HTTPS):   https://{local_ip}:{active_https_port}{fallback_note}")
    print(f" * Interactive API Docs:    http://{local_ip}:{port}/docs")
    print(f" * LAN Access PIN:          {pin_status}")
    print("=" * 65)
    print(" Press Ctrl+C to stop server.\n")

    if cert_pair is not None and active_https_port is not None:
        cert_path, key_path = cert_pair
        http_config = uvicorn.Config(
            "app.main:app",
            host=settings.HOST,
            port=port,
            reload=False,
        )
        https_config = uvicorn.Config(
            "app.main:app",
            host=settings.HOST,
            port=active_https_port,
            reload=False,
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
            lifespan="off",
        )
        http_server = uvicorn.Server(http_config)
        https_server = uvicorn.Server(https_config)
        https_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        asyncio.run(_serve_dual(http_server, https_server))
    else:
        uvicorn.run(
            "app.main:app",
            host=settings.HOST,
            port=port,
            reload=False,
        )
