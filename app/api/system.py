import os
import uuid
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.db.backup import backup_service
from app.db.migrations import get_current_schema_version
from app.schemas.system import (
    BackupResponse,
    BackupStatusResponse,
    BackupSummary,
    CleanupUnusedCropsResponse,
    HealthResponse,
)
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
def health_check(db: Session = Depends(get_db)) -> HealthResponse | JSONResponse:
    db.execute(text("SELECT 1"))
    bind = db.get_bind()
    schema_ver = get_current_schema_version(bind)
    uploads_ok = _check_uploads_writable()
    overall = "healthy" if uploads_ok and schema_ver >= 1 else "degraded"
    payload = HealthResponse(
        status=overall,
        database="ok",
        uploads_writable=uploads_ok,
        schema_version=schema_ver,
    )
    if overall != "healthy":
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload.model_dump())
    return payload


@router.post("/system/backup", response_model=BackupResponse, status_code=status.HTTP_201_CREATED)
def trigger_hot_backup(
    include_uploads: bool = Query(default=False, description="Bundle SQLite DB + uploads/ into a checksummed .zip archive."),
) -> BackupResponse:
    meta = (
        backup_service.create_full_backup_archive()
        if include_uploads
        else backup_service.create_hot_backup()
    )
    return BackupResponse(**meta)


@router.get("/system/backups", response_model=BackupStatusResponse)
def list_system_backups() -> BackupStatusResponse:
    entries = [BackupSummary(**item) for item in backup_service.list_backups()]
    return BackupStatusResponse(
        auto_backup_enabled=settings.AUTO_BACKUP_ENABLED,
        interval_hours=settings.AUTO_BACKUP_INTERVAL_HOURS,
        retention_count=settings.BACKUP_RETENTION_COUNT,
        latest_backup=entries[0] if entries else None,
        backups=entries,
    )


@router.get("/system/backups/{filename}")
def download_system_backup(filename: str) -> FileResponse:
    file_path = backup_service.resolve_backup_file(filename)
    media_type = "application/zip" if file_path.suffix.lower() == ".zip" else "application/octet-stream"
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
    )


@router.post("/system/cleanup-unused-crops", response_model=CleanupUnusedCropsResponse)
def cleanup_unused_crops(
    dry_run: bool = Query(default=True, description="Only count unused crops; pass false to delete them."),
    min_age_hours: float = Query(default=24.0, ge=1.0, le=24.0 * 365),
    db: Session = Depends(get_db),
) -> CleanupUnusedCropsResponse:
    count = photo_service.sweep_orphan_ai_crops(db, max_age_seconds=min_age_hours * 3600, dry_run=dry_run)
    return CleanupUnusedCropsResponse(dry_run=dry_run, unused_crops=count)

