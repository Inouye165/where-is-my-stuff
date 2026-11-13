from typing import Any
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.logging_config import logger, request_id_ctx


class AppError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    error_code: str = "app_error"

    def __init__(self, detail: str, status_code: int | None = None, error_code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "bad_request"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    error_code = "payload_too_large"


def _error_payload(code: str, detail: Any) -> dict[str, Any]:
    req_id = request_id_ctx.get("-")
    message = detail if isinstance(detail, str) else "Request validation failed."
    return {
        "detail": detail,
        "error": {
            "code": code,
            "message": message,
            "request_id": req_id,
        },
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("Domain error [%s] %s %s: %s", exc.error_code, request.method, request.url.path, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.error_code, exc.detail),
        )

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = f"http_{exc.status_code}"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, exc.detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        safe_errors = []
        for err in exc.errors():
            clean = {k: v for k, v in err.items() if k != "ctx"}
            if "ctx" in err and isinstance(err["ctx"], dict):
                clean["ctx"] = {ck: str(cv) for ck, cv in err["ctx"].items()}
            safe_errors.append(clean)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_payload("validation_error", safe_errors),
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.error("Database integrity error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_error_payload("integrity_error", "Database constraint violation."),
        )

    @app.exception_handler(OperationalError)
    async def handle_operational_error(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("Database operational error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_error_payload("database_unavailable", "Database is temporarily busy or unavailable."),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload("internal_error", "An unexpected internal error occurred."),
        )

