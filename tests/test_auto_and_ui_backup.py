import os
from pathlib import Path
import sqlite3
import time

from app.core.config import settings
from app.db.backup import backup_service


def test_scheduled_backup_respects_interval_and_creates_full_archive(tmp_path, monkeypatch):
    sample_db = tmp_path / "auto_src.db"
    conn = sqlite3.connect(str(sample_db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    uploads_dir = tmp_path / "uploads"
    (uploads_dir / "originals").mkdir(parents=True, exist_ok=True)
    (uploads_dir / "originals" / "sample.jpg").write_bytes(b"img-data")
    bdir = tmp_path / "backups"

    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{sample_db.as_posix()}")
    monkeypatch.setattr(settings, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(settings, "BACKUP_DIR", bdir)

    # First run with empty backup dir -> creates archive
    first = backup_service.maybe_create_scheduled_backup(interval_hours=24)
    assert first is not None
    assert first["filename"].endswith(".zip")
    archive_1 = bdir / first["filename"]
    assert archive_1.exists()

    # Immediate second run within 24h -> skipped
    skipped = backup_service.maybe_create_scheduled_backup(interval_hours=24)
    assert skipped is None

    # Age the existing backup past 24h -> creates a new archive
    old_mtime = time.time() - (25 * 3600)
    os.utime(archive_1, (old_mtime, old_mtime))
    second = backup_service.maybe_create_scheduled_backup(interval_hours=24)
    assert second is not None
    assert second["filename"] != first["filename"]


def test_backup_list_and_safe_download_endpoints(client, tmp_path, monkeypatch):
    sample_db = tmp_path / "api_list.db"
    conn = sqlite3.connect(str(sample_db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    uploads_dir = tmp_path / "uploads"
    (uploads_dir / "originals").mkdir(parents=True, exist_ok=True)
    (uploads_dir / "originals" / "pic.jpg").write_bytes(b"pic")
    bdir = tmp_path / "backups"

    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{sample_db.as_posix()}")
    monkeypatch.setattr(settings, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(settings, "BACKUP_DIR", bdir)

    empty_res = client.get("/api/v1/system/backups")
    assert empty_res.status_code == 200
    assert empty_res.json()["latest_backup"] is None
    assert empty_res.json()["backups"] == []

    created = client.post("/api/v1/system/backup?include_uploads=true")
    assert created.status_code == 201
    fname = created.json()["filename"]

    list_res = client.get("/api/v1/system/backups")
    assert list_res.status_code == 200
    body = list_res.json()
    assert body["auto_backup_enabled"] is settings.AUTO_BACKUP_ENABLED
    assert body["latest_backup"]["filename"] == fname
    assert body["latest_backup"]["includes_uploads"] is True

    dl = client.get(f"/api/v1/system/backups/{fname}")
    assert dl.status_code == 200
    assert "attachment" in dl.headers.get("content-disposition", "").lower()
    assert dl.content[:2] == b"PK"

    # Reject traversal / invalid filenames
    assert client.get("/api/v1/system/backups/evil.txt").status_code == 400
    assert client.get("/api/v1/system/backups/..%2Finventory.db").status_code in (400, 404)


def test_more_modal_has_delegated_one_click_backup_ui():
    root = Path(__file__).resolve().parent.parent / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    app_js = (root / "js" / "app.js").read_text(encoding="utf-8")

    assert 'id="more-backup-btn"' in html
    assert 'data-action="triggerBackupNow"' in html
    assert 'id="more-backup-status"' in html
    assert 'id="more-backup-download-link"' in html
    assert "triggerBackupNow" in app_js
    assert "registerActions" in app_js
