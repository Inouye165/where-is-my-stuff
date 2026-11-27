import asyncio
from contextlib import asynccontextmanager, suppress
import mimetypes
from typing import AsyncIterator, Optional
import anyio
from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from app.api import auth, documents, items, label_scan, locations, photos, search, system, unit_setup
from app.core.config import settings
from app.core.database import engine, get_db
from app.core.exceptions import NotFoundError, register_exception_handlers
from app.core.logging_config import configure_logging, logger
from app.core.middleware import RequestContextMiddleware, SecurityAndRateLimitMiddleware
from app.core.network import get_lan_ip
from app.core.security import is_auth_enabled
from app.db.backup import backup_service
from app.db.migrations import run_migrations
from app.services.unit_setup_service import unit_setup_service


class SafeUploadsStaticFiles(StaticFiles):
    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        if scope.get("type") == "http":
            path = str(scope.get("path", ""))
            if any(part.startswith(".") for part in path.split("/") if part):
                raise NotFoundError("File not found.")
        await super().__call__(scope, receive, send)


class FrontendStaticFiles(StaticFiles):
    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        if scope.get("type") == "http" and str(scope.get("path", "")).startswith("/api/"):
            raise NotFoundError("API endpoint not found.")
        await super().__call__(scope, receive, send)


def create_app() -> FastAPI:
    mimetypes.add_type("image/webp", ".webp")
    settings.init_directories()
    uploads_mount = SafeUploadsStaticFiles(directory=str(settings.UPLOAD_DIR))

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        settings.init_directories()
        uploads_mount.directory = str(settings.UPLOAD_DIR)
        uploads_mount.all_directories = [str(settings.UPLOAD_DIR)]
        version = run_migrations(engine)
        logger.info(
            "WIMS startup complete (schema_version=%d, host=%s, port=%d)",
            version,
            settings.HOST,
            settings.PORT,
        )

        backup_task: Optional[asyncio.Task[None]] = None
        should_auto_backup = (
            settings.AUTO_BACKUP_ENABLED
            and settings.AUTO_BACKUP_INTERVAL_HOURS > 0
            and engine.dialect.name == "sqlite"
            and bool(engine.url.database)
            and engine.url.database != ":memory:"
            and settings.sqlite_db_path is not None
        )
        if should_auto_backup:
            sleep_seconds = max(300, int(settings.AUTO_BACKUP_INTERVAL_HOURS) * 3600)

            async def _auto_backup_loop() -> None:
                while True:
                    try:
                        await anyio.to_thread.run_sync(backup_service.maybe_create_scheduled_backup)
                    except Exception as exc:
                        logger.warning("Scheduled automatic backup failed: %s", exc)
                    await asyncio.sleep(sleep_seconds)

            backup_task = asyncio.create_task(_auto_backup_loop())

        try:
            yield
        finally:
            if backup_task is not None:
                backup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await backup_task

    application = FastAPI(
        title=settings.APP_NAME,
        description="Physical inventory tracking with dual-photo visual context and presence auditing",
        version="1.1.0",
        lifespan=lifespan,
    )

    register_exception_handlers(application)
    # Middleware executes LIFO: SecurityAndRateLimitMiddleware runs inside RequestContextMiddleware
    # so 401/429 responses still receive X-Request-ID, security headers, and timing logs.
    application.add_middleware(SecurityAndRateLimitMiddleware)
    application.add_middleware(RequestContextMiddleware)

    if settings.CORS_ALLOWED_ORIGINS:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ALLOWED_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(system.router, prefix="/api/v1")
    application.include_router(locations.router, prefix="/api/v1")
    application.include_router(items.router, prefix="/api/v1")
    application.include_router(photos.router, prefix="/api/v1")
    application.include_router(search.router, prefix="/api/v1")
    application.include_router(unit_setup.router, prefix="/api/v1")
    application.include_router(label_scan.router, prefix="/api/v1")
    application.include_router(documents.router, prefix="/api/v1")

    @application.get("/phone", include_in_schema=False)
    def phone_capture_shortcut(
        request: Request,
        db: Session = Depends(get_db),
    ) -> RedirectResponse:
        if not is_auth_enabled():
            active = unit_setup_service.get_latest_active_session(
                db=db,
                host_header=request.headers.get("host"),
            )
            if active is not None:
                return RedirectResponse(url=active["mobile_url"], status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url="/setup.html", status_code=status.HTTP_302_FOUND)

    application.mount("/uploads", uploads_mount, name="uploads")

    static_dir = settings.BASE_DIR / "app" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    application.mount("/", FrontendStaticFiles(directory=str(static_dir), html=True), name="static")

    application.state.uploads_static = uploads_mount
    return application


app = create_app()
uploads_static = app.state.uploads_static


def get_local_ip() -> str:
    return get_lan_ip()
