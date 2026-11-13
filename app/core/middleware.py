import re
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from app.core.logging_config import logger, request_id_ctx

SAFE_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9._\-]{1,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming_id = request.headers.get("X-Request-ID", "").strip()
        req_id = incoming_id if SAFE_REQUEST_ID_RE.match(incoming_id) else uuid.uuid4().hex[:16]
        token = request_id_ctx.set(req_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if request.url.path.startswith("/api/"):
                logger.info(
                    "%s %s -> %.2fms",
                    request.method,
                    request.url.path,
                    elapsed_ms,
                )
            request_id_ctx.reset(token)

        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        return response

