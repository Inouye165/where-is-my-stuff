import os
import uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.db.backup import backup_service
from app.db.migrations import get_current_schema_version
from app.schemas.system import BackupResponse, HealthResponse
from app.services.photo_service import photo_service

router = APIRouter(tags=["System"])


def _check_uploads_writable() -> bool:
    probe = settings.UPLOAD_DIR / f".health_probe_{os.getpid()}_{uuid.uuid4().hex}"
    try:
        settings.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        settings.THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


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


@router.post("/system/cleanup-unused-crops")
def cleanup_unused_crops(
    dry_run: bool = Query(default=True, description="Only count unused crops; pass false to delete them."),
    min_age_hours: float = Query(default=24.0, ge=1.0, le=24.0 * 365),
    db: Session = Depends(get_db),
) -> dict:
    count = photo_service.sweep_orphan_ai_crops(db, max_age_seconds=min_age_hours * 3600, dry_run=dry_run)
    return {"dry_run": dry_run, "unused_crops": count}

