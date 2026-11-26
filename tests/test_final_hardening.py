import hashlib
import json
import sqlite3
import zipfile

import pytest

from app.core.config import settings
from app.db.migrations import run_migrations


def test_uploads_rejects_dotfiles_and_temp_files(client):
    settings.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    probe = settings.UPLOAD_DIR / ".health_probe_secret"
    tmp_orig = settings.ORIGINALS_DIR / ".temp_secret.tmp"
    probe.write_bytes(b"secret")
    tmp_orig.write_bytes(b"tmp")
    try:
        assert client.get("/uploads/.health_probe_secret").status_code == 404
        assert client.get("/uploads/originals/.temp_secret.tmp").status_code == 404
    finally:
        probe.unlink(missing_ok=True)
        tmp_orig.unlink(missing_ok=True)


def test_create_app_factory_returns_configured_fastapi():
    from fastapi import FastAPI
    from app.main import create_app

    new_app = create_app()
    assert isinstance(new_app, FastAPI)
    paths = set(new_app.openapi()["paths"].keys())
    assert "/api/v1/health" in paths
    assert "/api/v1/items/page" in paths


def test_file_backed_migrations_use_and_clean_cross_process_lock(tmp_path):
    from sqlalchemy import create_engine

    db_file = tmp_path / "locked_migrate.db"
    lock_file = tmp_path / "locked_migrate.db.migrate.lock"
    eng = create_engine(f"sqlite:///{db_file.as_posix()}")
    try:
        v = run_migrations(eng)
        assert v >= 8
        assert not lock_file.exists(), "Cross-process migration lock file was not cleaned up"
    finally:
        eng.dispose()


def test_full_backup_archive_includes_db_uploads_and_sha256_manifest(client, tmp_path, monkeypatch):
    sample_db = tmp_path / "src_inventory.db"
    conn = sqlite3.connect(str(sample_db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT);")
    conn.execute("INSERT INTO t (val) VALUES ('ok');")
    conn.commit()
    conn.close()

    uploads_dir = tmp_path / "uploads"
    (uploads_dir / "originals").mkdir(parents=True, exist_ok=True)
    (uploads_dir / "originals" / "part.jpg").write_bytes(b"jpeg-bytes")
    (uploads_dir / "originals" / ".hidden.tmp").write_bytes(b"ignore-me")
    bdir = tmp_path / "backups"

    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{sample_db.as_posix()}")
    monkeypatch.setattr(settings, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(settings, "BACKUP_DIR", bdir)

    resp = client.post("/api/v1/system/backup?include_uploads=true")
    assert resp.status_code == 201, resp.text
    meta = resp.json()
    assert meta["filename"].endswith(".zip")
    archive_path = bdir / meta["filename"]
    assert archive_path.exists()

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = set(zf.namelist())
        assert "inventory.db" in names
        assert "uploads/originals/part.jpg" in names
        assert "uploads/originals/.hidden.tmp" not in names
        assert "manifest.json" in names

        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["files"]["uploads/originals/part.jpg"] == hashlib.sha256(b"jpeg-bytes").hexdigest()
        assert "inventory.db" in manifest["files"]


def test_quick_entry_parser_module_and_service_delegation():
    from app.services.inventory_service import inventory_service
    from app.services.quick_entry_parser import parse_quick_entry_lines

    sample = "1. 5x 10k Resistor — 1/4W\n- 2 rolls Kapton Tape"
    direct = parse_quick_entry_lines(sample, location_id=7)
    via_service = inventory_service.parse_quick_entry_lines(sample, location_id=7)
    assert len(direct) == 2
    assert direct == via_service
    assert direct[0].name == "10k Resistor"
    assert direct[0].quantity == 5
    assert direct[0].description == "1/4W"
    assert direct[1].unit == "rolls"


def test_env_int_validation_reports_variable_name(monkeypatch):
    from app.core.config import _env_int

    monkeypatch.setenv("PORT", "not_a_number")
    with pytest.raises(ValueError, match="PORT"):
        _env_int("PORT", 8000)
