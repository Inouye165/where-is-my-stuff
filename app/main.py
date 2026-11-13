from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api import items, locations, photos, search, system
from app.core.config import settings
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import configure_logging, logger
from app.core.middleware import RequestContextMiddleware
from app.core.network import get_lan_ip
from app.db.migrations import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings.init_directories()
    version = run_migrations(engine)
    logger.info("WIMS startup complete (schema_version=%d, host=%s, port=%d)", version, settings.HOST, settings.PORT)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Physical inventory tracking with dual-photo visual context and presence auditing",
    version="1.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.add_middleware(RequestContextMiddleware)

if settings.CORS_ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

app.include_router(system.router, prefix="/api/v1")
app.include_router(locations.router, prefix="/api/v1")
app.include_router(locations.router, prefix="/api/v1/containers", tags=["Containers"])
app.include_router(items.router, prefix="/api/v1")
app.include_router(photos.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")

settings.init_directories()
app.mount("/uploads", StaticFiles(directory=str(settings.UPLOAD_DIR)), name="uploads")

static_dir = settings.BASE_DIR / "app" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def get_local_ip() -> str:
    return get_lan_ip()
