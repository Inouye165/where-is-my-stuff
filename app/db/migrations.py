from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time
from typing import Callable, Iterator, List, Optional, Tuple
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from app.core.database import Base
from app.core.logging_config import logger
import app.models  # noqa: F401 - ensure all models are registered on Base.metadata


MigrationStep = Tuple[int, str, Callable[[Connection], None]]


def _migration_001_initial_schema(conn: Connection) -> None:
    Base.metadata.create_all(bind=conn)

    inspector = inspect(conn)
    if "items" in inspector.get_table_names():
        existing_cols = {col["name"] for col in inspector.get_columns("items")}
        if "status" not in existing_cols:
            conn.execute(text("ALTER TABLE items ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'stored'"))
        if "status_note" not in existing_cols:
            conn.execute(text("ALTER TABLE items ADD COLUMN status_note VARCHAR(250)"))


def _migration_002_performance_indexes(conn: Connection) -> None:
    index_statements = [
        "CREATE INDEX IF NOT EXISTS ix_locations_parent_id ON locations (parent_id)",
        "CREATE INDEX IF NOT EXISTS ix_locations_location_type ON locations (location_type)",
        "CREATE INDEX IF NOT EXISTS ix_locations_name ON locations (name)",
        "CREATE INDEX IF NOT EXISTS ix_items_location_id ON items (location_id)",
        "CREATE INDEX IF NOT EXISTS ix_items_status ON items (status)",
        "CREATE INDEX IF NOT EXISTS ix_items_category ON items (category)",
        "CREATE INDEX IF NOT EXISTS ix_items_last_seen_at ON items (last_seen_at)",
        "CREATE INDEX IF NOT EXISTS ix_items_updated_at ON items (updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_items_serial_or_part_number ON items (serial_or_part_number)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_item_id ON audit_logs (item_id)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_location_id ON audit_logs (location_id)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_recorded_at ON audit_logs (recorded_at)",
    ]
    for stmt in index_statements:
        conn.execute(text(stmt))


def _migration_003_location_grid_layout(conn: Connection) -> None:
    inspector = inspect(conn)
    if "locations" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("locations")}
    new_cols = [
        ("grid_rows", "INTEGER"),
        ("grid_cols", "INTEGER"),
        ("grid_config", "TEXT"),
        ("grid_row", "INTEGER"),
        ("grid_col", "INTEGER"),
        ("grid_row_span", "INTEGER"),
        ("grid_col_span", "INTEGER"),
    ]
    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            conn.execute(text(f"ALTER TABLE locations ADD COLUMN {col_name} {col_type}"))


def _migration_004_location_photos(conn: Connection) -> None:
    Base.metadata.create_all(bind=conn)
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_location_photos_location_id ON location_photos (location_id)"))

    inspector = inspect(conn)
    if "locations" not in inspector.get_table_names() or "location_photos" not in inspector.get_table_names():
        return

    existing_pairs = {
        (int(r[0]), str(r[1]))
        for r in conn.execute(text("SELECT location_id, photo_path FROM location_photos")).fetchall()
    }
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(text("SELECT id, container_photo, context_photo FROM locations")).fetchall()
    for loc_id, container_photo, context_photo in rows:
        for photo_path, photo_type in ((container_photo, "container"), (context_photo, "context")):
            if photo_path and (int(loc_id), str(photo_path)) not in existing_pairs:
                conn.execute(
                    text(
                        "INSERT INTO location_photos (location_id, photo_path, thumbnail_path, photo_type, created_at) "
                        "VALUES (:location_id, :photo_path, :thumbnail_path, :photo_type, :created_at)"
                    ),
                    {
                        "location_id": int(loc_id),
                        "photo_path": str(photo_path),
                        "thumbnail_path": None,
                        "photo_type": photo_type,
                        "created_at": now_iso,
                    },
                )
                existing_pairs.add((int(loc_id), str(photo_path)))


def _migration_005_item_attachments(conn: Connection) -> None:
    Base.metadata.create_all(bind=conn)
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_item_attachments_item_id ON item_attachments (item_id)"))


def _migration_006_location_label(conn: Connection) -> None:
    inspector = inspect(conn)
    if "locations" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("locations")}
    if "label" not in existing_cols:
        conn.execute(text("ALTER TABLE locations ADD COLUMN label VARCHAR(40)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_locations_label ON locations (label)"))


def _migration_007_grid_slot_unique_index(conn: Connection) -> None:
    inspector = inspect(conn)
    if "locations" not in inspector.get_table_names():
        return
    # Skip creating the unique index if legacy rows already contain duplicate grid coords.
    dup = conn.execute(
        text(
            "SELECT 1 FROM locations "
            "WHERE parent_id IS NOT NULL AND grid_row IS NOT NULL AND grid_col IS NOT NULL "
            "GROUP BY parent_id, grid_row, grid_col HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).fetchone()
    if dup is None:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_locations_parent_grid_pos "
                "ON locations (parent_id, grid_row, grid_col) "
                "WHERE parent_id IS NOT NULL AND grid_row IS NOT NULL AND grid_col IS NOT NULL"
            )
        )


def _migration_008_persistent_sessions(conn: Connection) -> None:
    Base.metadata.create_all(bind=conn)
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_capture_sessions_expires_at ON capture_sessions (expires_at)"))
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_unit_setup_sessions_expires_at ON unit_setup_sessions (expires_at)")
    )


def _migration_009_label_scan_sessions(conn: Connection) -> None:
    Base.metadata.create_all(bind=conn)
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_label_scan_sessions_expires_at ON label_scan_sessions (expires_at)")
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_label_scan_sessions_container_id ON label_scan_sessions (container_id)")
    )


def _migration_010_photo_path_indexes(conn: Connection) -> None:
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_items_item_photo ON items (item_photo)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_locations_container_photo ON locations (container_photo)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_locations_context_photo ON locations (context_photo)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_location_photos_photo_path ON location_photos (photo_path)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_item_attachments_file_path ON item_attachments (file_path)"))


MIGRATIONS: List[MigrationStep] = [
    (1, "001_initial_schema", _migration_001_initial_schema),
    (2, "002_performance_indexes", _migration_002_performance_indexes),
    (3, "003_location_grid_layout", _migration_003_location_grid_layout),
    (4, "004_location_photos", _migration_004_location_photos),
    (5, "005_item_attachments", _migration_005_item_attachments),
    (6, "006_location_label", _migration_006_location_label),
    (7, "007_grid_slot_unique_index", _migration_007_grid_slot_unique_index),
    (8, "008_persistent_sessions", _migration_008_persistent_sessions),
    (9, "009_label_scan_sessions", _migration_009_label_scan_sessions),
    (10, "010_photo_path_indexes", _migration_010_photo_path_indexes),
]


def _ensure_migrations_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                applied_at VARCHAR(64) NOT NULL
            )
            """
        )
    )


def get_current_schema_version(engine: Engine) -> int:
    with engine.connect() as conn:
        inspector = inspect(conn)
        if "schema_migrations" not in inspector.get_table_names():
            return 0
        row = conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")).scalar()
        return int(row or 0)


_MIGRATION_LOCK = threading.Lock()


@contextmanager
def _cross_process_migration_lock(engine: Engine, timeout_seconds: float = 60.0) -> Iterator[None]:
    """Acquire an exclusive lock file for file-backed SQLite databases across worker processes."""
    lock_path: Optional[Path] = None
    if engine.dialect.name == "sqlite" and engine.url.database and engine.url.database != ":memory:":
        lock_path = Path(f"{engine.url.database}.migrate.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if lock_path.exists() and (time.time() - lock_path.stat().st_mtime) > timeout_seconds:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for migration lock: {lock_path}") from None
                time.sleep(0.05)

    try:
        yield
    finally:
        if lock_path is not None:
            lock_path.unlink(missing_ok=True)


def run_migrations(engine: Engine) -> int:
    with _MIGRATION_LOCK, _cross_process_migration_lock(engine):
        with engine.begin() as conn:
            if engine.dialect.name == "sqlite":
                conn.execute(text("BEGIN IMMEDIATE"))
            elif engine.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(79194421)"))
            _ensure_migrations_table(conn)
            conn.execute(text("DELETE FROM schema_migrations WHERE version < 0"))
            applied_rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
            applied_versions = {int(r[0]) for r in applied_rows}

            for version, name, step_fn in MIGRATIONS:
                if version in applied_versions:
                    continue
                logger.info("Applying database migration v%d (%s)", version, name)
                step_fn(conn)
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    text(
                        "INSERT INTO schema_migrations (version, name, applied_at) "
                        "VALUES (:version, :name, :applied_at)"
                    ),
                    {"version": version, "name": name, "applied_at": now_iso},
                )

            current = conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")).scalar()
            return int(current or 0)

