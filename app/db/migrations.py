from datetime import datetime, timezone
from typing import Callable, List, Tuple
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


MIGRATIONS: List[MigrationStep] = [
    (1, "001_initial_schema", _migration_001_initial_schema),
    (2, "002_performance_indexes", _migration_002_performance_indexes),
    (3, "003_location_grid_layout", _migration_003_location_grid_layout),
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


def run_migrations(engine: Engine) -> int:
    with engine.begin() as conn:
        _ensure_migrations_table(conn)
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

