from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Optional
from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.core.logging_config import logger


class BackupService:
    def create_hot_backup(
        self,
        source_db_path: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        max_keep: Optional[int] = None,
    ) -> dict[str, Any]:
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

        src_conn: Optional[sqlite3.Connection] = None
        dst_conn: Optional[sqlite3.Connection] = None
        try:
            src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=10.0)
            dst_conn = sqlite3.connect(str(temp_path), timeout=10.0)
            src_conn.backup(dst_conn)
            dst_conn.commit()

            integrity_row = dst_conn.execute("PRAGMA integrity_check;").fetchone()
            integrity_ok = bool(integrity_row and integrity_row[0] == "ok")
        finally:
            if dst_conn is not None:
                dst_conn.close()
            if src_conn is not None:
                src_conn.close()

        if not integrity_ok:
            temp_path.unlink(missing_ok=True)
            raise BadRequestError("Backup integrity check failed.")

        os.replace(temp_path, final_path)
        size_bytes = final_path.stat().st_size
        self._prune_old_backups(target_dir, keep)

        logger.info("Created SQLite hot backup %s (%d bytes)", final_path.name, size_bytes)
        return {
            "filename": filename,
            "size_bytes": size_bytes,
            "integrity_ok": integrity_ok,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _prune_old_backups(backup_dir: Path, max_keep: int) -> None:
        if max_keep <= 0:
            return
        backups = sorted(
            backup_dir.glob("inventory_backup_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[max_keep:]:
            stale.unlink(missing_ok=True)


backup_service = BackupService()

