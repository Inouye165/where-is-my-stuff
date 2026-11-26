from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Optional
import uuid
import zipfile
from app.core.config import settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.logging_config import logger

_SAFE_BACKUP_NAME_RE = re.compile(r"^inventory_(?:full_)?backup_[0-9TZ]+_[a-f0-9]+\.(?:db|zip)$")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class BackupService:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _write_verified_sqlite_snapshot(self, src: Path, target_db: Path) -> bool:
        src_conn: Optional[sqlite3.Connection] = None
        dst_conn: Optional[sqlite3.Connection] = None
        completed = False
        integrity_ok = False
        try:
            src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=10.0)
            dst_conn = sqlite3.connect(str(target_db), timeout=10.0)
            src_conn.backup(dst_conn)
            dst_conn.commit()
            integrity_row = dst_conn.execute("PRAGMA integrity_check;").fetchone()
            integrity_ok = bool(integrity_row and integrity_row[0] == "ok")
            completed = True
        finally:
            if dst_conn is not None:
                dst_conn.close()
            if src_conn is not None:
                src_conn.close()
            if not completed:
                target_db.unlink(missing_ok=True)
        return integrity_ok

    def create_hot_backup(
        self,
        source_db_path: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        max_keep: Optional[int] = None,
    ) -> dict[str, Any]:
        with self._lock:
            src = source_db_path or settings.sqlite_db_path
            if src is None or not src.exists():
                raise BadRequestError("Hot SQLite backup requires an existing file-backed SQLite database.")

            target_dir = backup_dir or settings.BACKUP_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            keep = max_keep if max_keep is not None else settings.BACKUP_RETENTION_COUNT

            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"inventory_backup_{ts}_{uuid.uuid4().hex[:6]}.db"
            temp_path = target_dir / f".{filename}.tmp"
            final_path = target_dir / filename

            integrity_ok = self._write_verified_sqlite_snapshot(src, temp_path)
            if not integrity_ok:
                temp_path.unlink(missing_ok=True)
                raise BadRequestError("Backup integrity check failed.")

            os.replace(temp_path, final_path)
            size_bytes = final_path.stat().st_size
            self._prune_old_backups(target_dir, keep, pattern="inventory_backup_*.db")

            logger.info("Created SQLite hot backup %s (%d bytes)", final_path.name, size_bytes)
            return {
                "filename": filename,
                "size_bytes": size_bytes,
                "integrity_ok": integrity_ok,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

    def create_full_backup_archive(
        self,
        source_db_path: Optional[Path] = None,
        upload_dir: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        max_keep: Optional[int] = None,
    ) -> dict[str, Any]:
        """Bundle a verified SQLite hot backup, uploaded media/attachments, and SHA-256 manifest into a zip."""
        with self._lock:
            return self._create_full_backup_archive_unlocked(
                source_db_path=source_db_path,
                upload_dir=upload_dir,
                backup_dir=backup_dir,
                max_keep=max_keep,
            )

    def _create_full_backup_archive_unlocked(
        self,
        source_db_path: Optional[Path] = None,
        upload_dir: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        max_keep: Optional[int] = None,
    ) -> dict[str, Any]:
        src = source_db_path or settings.sqlite_db_path
        if src is None or not src.exists():
            raise BadRequestError("Full backup requires an existing file-backed SQLite database.")

        target_dir = backup_dir or settings.BACKUP_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        uploads_root = upload_dir or settings.UPLOAD_DIR
        keep = max_keep if max_keep is not None else settings.BACKUP_RETENTION_COUNT

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = uuid.uuid4().hex[:6]
        db_stage_path = target_dir / f".stage_inventory_{ts}_{suffix}.db"
        zip_filename = f"inventory_full_backup_{ts}_{suffix}.zip"
        zip_tmp_path = target_dir / f".{zip_filename}.tmp"
        zip_final_path = target_dir / zip_filename

        try:
            integrity_ok = self._write_verified_sqlite_snapshot(src, db_stage_path)
            if not integrity_ok:
                raise BadRequestError("Backup integrity check failed.")

            manifest_files: dict[str, str] = {"inventory.db": _sha256_file(db_stage_path)}
            with zipfile.ZipFile(zip_tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(db_stage_path, arcname="inventory.db")
                if uploads_root.exists():
                    for file_path in sorted(uploads_root.rglob("*")):
                        if not file_path.is_file():
                            continue
                        rel_parts = file_path.relative_to(uploads_root).parts
                        if any(part.startswith(".") for part in rel_parts):
                            continue
                        arcname = f"uploads/{'/'.join(rel_parts)}"
                        manifest_files[arcname] = _sha256_file(file_path)
                        zf.write(file_path, arcname=arcname)

                manifest_payload = {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "integrity_ok": True,
                    "files": manifest_files,
                }
                zf.writestr("manifest.json", json.dumps(manifest_payload, indent=2))

            os.replace(zip_tmp_path, zip_final_path)
        except Exception:
            zip_tmp_path.unlink(missing_ok=True)
            raise
        finally:
            db_stage_path.unlink(missing_ok=True)

        size_bytes = zip_final_path.stat().st_size
        self._prune_old_backups(target_dir, keep, pattern="inventory_full_backup_*.zip")
        logger.info("Created full backup archive %s (%d bytes, %d files)", zip_final_path.name, size_bytes, len(manifest_files))
        return {
            "filename": zip_filename,
            "size_bytes": size_bytes,
            "integrity_ok": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def list_backups(self, backup_dir: Optional[Path] = None) -> list[dict[str, Any]]:
        target_dir = backup_dir or settings.BACKUP_DIR
        if not target_dir.exists():
            return []

        candidates: list[Path] = []
        for path in target_dir.iterdir():
            if path.is_file() and _SAFE_BACKUP_NAME_RE.match(path.name):
                candidates.append(path)

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[dict[str, Any]] = []
        for path in candidates:
            st = path.stat()
            out.append(
                {
                    "filename": path.name,
                    "size_bytes": int(st.st_size),
                    "created_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    "includes_uploads": path.suffix.lower() == ".zip",
                }
            )
        return out

    def resolve_backup_file(self, filename: str, backup_dir: Optional[Path] = None) -> Path:
        if not filename or not _SAFE_BACKUP_NAME_RE.match(filename):
            raise BadRequestError("Invalid backup filename.")
        target_dir = (backup_dir or settings.BACKUP_DIR).resolve()
        candidate = (target_dir / filename).resolve()
        if not candidate.is_relative_to(target_dir):
            raise BadRequestError("Invalid backup filename.")
        if not candidate.exists() or not candidate.is_file():
            raise NotFoundError("Backup archive not found.")
        return candidate

    def maybe_create_scheduled_backup(
        self,
        *,
        interval_hours: Optional[float] = None,
        source_db_path: Optional[Path] = None,
        upload_dir: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        max_keep: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        hours = float(interval_hours if interval_hours is not None else settings.AUTO_BACKUP_INTERVAL_HOURS)
        if hours <= 0:
            return None
        interval_seconds = hours * 3600.0
        target_dir = backup_dir or settings.BACKUP_DIR

        with self._lock:
            if target_dir.exists():
                archives = sorted(
                    (
                        p
                        for p in target_dir.glob("inventory_full_backup_*.zip")
                        if p.is_file() and _SAFE_BACKUP_NAME_RE.match(p.name)
                    ),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if archives and (time.time() - archives[0].stat().st_mtime) < interval_seconds:
                    return None
            return self._create_full_backup_archive_unlocked(
                source_db_path=source_db_path,
                upload_dir=upload_dir,
                backup_dir=target_dir,
                max_keep=max_keep,
            )

    @staticmethod
    def _prune_old_backups(backup_dir: Path, max_keep: int, pattern: str = "inventory_backup_*.db") -> None:
        if max_keep <= 0:
            return

        def _mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except FileNotFoundError:
                return 0.0

        backups = sorted(
            backup_dir.glob(pattern),
            key=_mtime,
            reverse=True,
        )
        for stale in backups[max_keep:]:
            stale.unlink(missing_ok=True)


backup_service = BackupService()
