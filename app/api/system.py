import os
from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.db.backup import backup_service
from app.db.migrations import get_current_schema_version
from app.schemas.system import BackupResponse, HealthResponse

router = APIRouter(tags=["System"])


def _check_uploads_writable() -> bool:
    try:
        settings.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        settings.THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
        probe = settings.UPLOAD_DIR / f".health_probe_{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(text("SELECT 1"))
    bind = db.get_bind()
    schema_ver = get_current_schema_version(bind)
    uploads_ok = _check_uploads_writable()
    overall = "healthy" if uploads_ok and schema_ver >= 1 else "degraded"
    return HealthResponse(
        status=overall,
        database="ok",
        uploads_writable=uploads_ok,
        schema_version=schema_ver,
    )


@router.post("/system/backup", response_model=BackupResponse, status_code=status.HTTP_201_CREATED)
def trigger_hot_backup() -> BackupResponse:
    meta = backup_service.create_hot_backup()
    return BackupResponse(**meta)

