import re
import time
import uuid
from fastapi import status
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from app.core.config import settings
from app.core.exceptions import _error_payload
from app.core.logging_config import logger, request_id_ctx
from app.core.rate_limit import match_rate_limit_bucket, rate_limiter
from app.core.security import (
    is_auth_enabled,
    is_public_or_qr_scoped_path,
    is_request_authenticated,
)

SAFE_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9._\-]{1,64}$")
_DOWNLOAD_ONLY_ATTACHMENT_EXTS = (".zip", ".doc", ".docx", ".xls", ".xlsx")
_EMBEDDABLE_DOC_EXTS = (".pdf", ".txt", ".md", ".csv", ".json")


class SecurityAndRateLimitMiddleware:
    """Pure ASGI middleware enforcing optional PIN auth and per-IP sliding-window rate limits."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET"))
        path = str(scope.get("path", ""))
        client_tuple = scope.get("client")
        client_ip = str(client_tuple[0]) if client_tuple and client_tuple[0] else "unknown"

        if settings.RATE_LIMIT_ENABLED:
            bucket_info = match_rate_limit_bucket(method, path)
            if bucket_info is not None:
                bucket, limit = bucket_info
                allowed, retry_after = rate_limiter.check(client_ip, bucket, limit)
                if not allowed:
                    resp = JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content=_error_payload(
                            "rate_limited",
                            "Too many requests. Please slow down and try again.",
                        ),
                        headers={"Retry-After": str(retry_after)},
                    )
                    await resp(scope, receive, send)
                    return

        if is_auth_enabled() and not is_public_or_qr_scoped_path(method, path):
            headers = Headers(scope=scope)
            if not is_request_authenticated(headers, client_ip):
                resp = JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content=_error_payload("unauthorized", "Authentication required."),
                )
                await resp(scope, receive, send)
                return

        await self.app(scope, receive, send)


class RequestContextMiddleware:
    """Pure ASGI middleware for request-ID correlation, timing logs, and security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming_id = (headers.get("X-Request-ID") or "").strip()
        req_id = incoming_id if SAFE_REQUEST_ID_RE.match(incoming_id) else uuid.uuid4().hex[:16]
        token = request_id_ctx.set(req_id)
        start = time.perf_counter()
        method = scope.get("method", "")
        raw_path = str(scope.get("path", ""))
        lower_path = raw_path.lower()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 200))
                resp_headers = MutableHeaders(scope=message)
                resp_headers["X-Request-ID"] = req_id
                resp_headers["X-Content-Type-Options"] = "nosniff"
                resp_headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                resp_headers["Permissions-Policy"] = "geolocation=(), microphone=()"

                if lower_path.startswith("/uploads/") and lower_path.endswith(_EMBEDDABLE_DOC_EXTS):
                    resp_headers["X-Frame-Options"] = "SAMEORIGIN"
                    resp_headers["Content-Security-Policy"] = "frame-ancestors 'self'"
                else:
                    resp_headers["X-Frame-Options"] = "DENY"

                if lower_path.startswith("/uploads/") and lower_path.endswith(".svg"):
                    resp_headers["Content-Security-Policy"] = (
                        "default-src 'none'; style-src 'unsafe-inline'; sandbox"
                    )
                elif lower_path in ("/capture.html", "/setup.html", "/label-capture.html"):
                    resp_headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
                        "base-uri 'self'; frame-ancestors 'none'"
                    )
                elif lower_path in ("/", "/index.html"):
                    resp_headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
                        "base-uri 'self'; frame-ancestors 'none'"
                    )
                if lower_path.startswith("/uploads/attachments/") and lower_path.endswith(
                    _DOWNLOAD_ONLY_ATTACHMENT_EXTS
                ):
                    resp_headers.setdefault("Content-Disposition", "attachment")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if raw_path.startswith("/api/"):
                logger.info("%s %s -> %d (%.2fms)", method, raw_path, status_code, elapsed_ms)
            request_id_ctx.reset(token)

