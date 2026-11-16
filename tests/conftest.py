from pathlib import Path
import shutil
import tempfile
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.db.migrations import run_migrations
import app.main as main_module
from app.main import app

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def set_test_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


main_module.engine = test_engine
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_directories():
    temp_dir = tempfile.mkdtemp()
    root = Path(temp_dir)
    settings.DATA_DIR = root
    settings.UPLOAD_DIR = root / "uploads"
    settings.ORIGINALS_DIR = settings.UPLOAD_DIR / "originals"
    settings.THUMBNAILS_DIR = settings.UPLOAD_DIR / "thumbnails"
    settings.ATTACHMENTS_DIR = settings.UPLOAD_DIR / "attachments"
    settings.BACKUP_DIR = root / "backups"
    settings.LOG_DIR = root / "logs"
    settings.init_directories()
    from app.core.logging_config import configure_logging
    configure_logging()
    yield
    import logging
    from logging.handlers import RotatingFileHandler
    wims_logger = logging.getLogger("wims")
    for handler in list(wims_logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            wims_logger.removeHandler(handler)
            handler.close()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture()
def db_session():
    run_migrations(test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        with test_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
