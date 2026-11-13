import io
import sqlite3
import subprocess
import shutil
from pathlib import Path
import pytest
from PIL import Image


def test_bulk_line_parser_edge_cases():
    from app.services.inventory_service import inventory_service

    raw = """
    1. 4x M3 Brass Standoffs
    - Needle Nose Pliers
    • 10 pcs 100kΩ Resistors
    Blue Masking Tape (3 rolls)
    Heat Shrink Tubing - 2 packs
    
    """
    parsed = inventory_service.parse_quick_entry_lines(raw, location_id=7)
    assert len(parsed) == 5
    assert parsed[0].name == "M3 Brass Standoffs" and parsed[0].quantity == 4
    assert parsed[1].name == "Needle Nose Pliers" and parsed[1].quantity == 1
    assert parsed[2].name == "100kΩ Resistors" and parsed[2].quantity == 10 and parsed[2].unit == "pcs"
    assert parsed[3].name == "Blue Masking Tape" and parsed[3].quantity == 3 and parsed[3].unit == "rolls"
    assert parsed[4].name == "Heat Shrink Tubing" and parsed[4].quantity == 2 and parsed[4].unit == "packs"


def test_photo_service_rejects_polyglot_and_oversized_streams(monkeypatch):
    from fastapi import UploadFile
    from app.services.photo_service import photo_service
    from app.core.config import settings
    from app.core.exceptions import PayloadTooLargeError, BadRequestError

    # 1. SVG/script disguised with .jpg extension must be rejected
    polyglot = io.BytesIO(b"<svg onload='alert(1)'><script>alert(1)</script></svg>")
    up_fake = UploadFile(filename="trick.jpg", file=polyglot)
    with pytest.raises(Exception) as exc_info:
        photo_service.process_and_save(up_fake)
    assert getattr(exc_info.value, "status_code", 400) == 400

    # 2. Stream exceeding MAX_IMAGE_UPLOAD_SIZE_MB must raise 413 PayloadTooLargeError
    monkeypatch.setattr(settings, "MAX_IMAGE_UPLOAD_SIZE_MB", 1)
    two_mb = io.BytesIO(b"\xff\xd8\xff\xe0" + (b"0" * (2 * 1024 * 1024)))
    up_big = UploadFile(filename="huge.jpg", file=two_mb)
    with pytest.raises(PayloadTooLargeError) as big_exc:
        photo_service.process_and_save(up_big)
    assert big_exc.value.status_code == 413

    # 3. Path traversal check must block '../' escapes
    with pytest.raises(BadRequestError):
        photo_service.resolve_safe_upload_path("/uploads/originals/../../inventory.db")


def test_photo_exif_orientation_transpose():
    from fastapi import UploadFile
    from app.services.photo_service import photo_service
    from app.core.config import settings

    # Create 200x100 image with EXIF Orientation = 6 (90 CW -> becomes 100x200)
    img = Image.new("RGB", (200, 100), color=(20, 100, 200))
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation tag
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    buf.seek(0)

    res = photo_service.process_and_save(UploadFile(filename="rotated.jpg", file=buf))
    saved = settings.ORIGINALS_DIR / res["filename"]
    assert saved.exists()
    with Image.open(saved) as out_img:
        assert out_img.width == 100 and out_img.height == 200


def test_sqlite_hot_backup_creates_consistent_snapshot(tmp_path):
    from app.db.backup import backup_service

    src_db = tmp_path / "live_test.db"
    backup_dir = tmp_path / "backups"

    conn = sqlite3.connect(str(src_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, label TEXT);")
    conn.execute("INSERT INTO sample (label) VALUES ('ESP32'), ('Drill');")
    conn.commit()
    conn.close()

    meta = backup_service.create_hot_backup(source_db_path=src_db, backup_dir=backup_dir, max_keep=3)
    assert meta["integrity_ok"] is True
    assert meta["size_bytes"] > 0

    bak_path = backup_dir / meta["filename"]
    assert bak_path.exists()

    bconn = sqlite3.connect(str(bak_path))
    rows = bconn.execute("SELECT label FROM sample ORDER BY id").fetchall()
    bconn.close()
    assert [r[0] for r in rows] == ["ESP32", "Drill"]


def test_frontend_dom_escaping_and_es_modules():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    js_files = sorted(js_dir.glob("*.js"))
    assert len(js_files) >= 5, f"Expected modular JS files under {js_dir}, found {[f.name for f in js_files]}"

    # 1. Syntax check every ES module
    for js_file in js_files:
        proc = subprocess.run([node_exe, "--check", str(js_file)], capture_output=True, text=True)
        assert proc.returncode == 0, f"Syntax error in {js_file.name}:\n{proc.stderr}"

    # 2. Verify all relative ES module imports resolve on disk
    import re
    for js_file in js_files:
        text = js_file.read_text(encoding="utf-8")
        imports = re.findall(r"""from\s+['"](\./[^'"]+)['"]""", text)
        for rel in imports:
            resolved = (js_file.parent / rel).resolve()
            assert resolved.exists(), f"Broken import '{rel}' in {js_file.name}"

    # 3. Execute dom.js in Node to verify XSS neutralization
    dom_module_url = (js_dir / "dom.js").as_uri()
    script = f"""
      import {{ escapeHtml, escapeJsStr, sanitizePhotoUrl }} from '{dom_module_url}';
      const xss = '<img src=x onerror="alert(1)"> & \\'test\\'';
      const escaped = escapeHtml(xss);
      if (escaped.includes('<') || escaped.includes('>') || escaped.includes('"')) {{
        process.exit(2);
      }}
      if (sanitizePhotoUrl('javascript:alert(1)') !== '') process.exit(3);
      if (sanitizePhotoUrl('data:text/html,<script>alert(1)</script>') !== '') process.exit(4);
      if (sanitizePhotoUrl('/uploads/originals/abc.jpg') !== '/uploads/originals/abc.jpg') process.exit(5);
      const jsEsc = escapeJsStr("a'b\\"c<script>");
      if (jsEsc.includes('<') || jsEsc.includes('>')) process.exit(6);
    """
    run_dom = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert run_dom.returncode == 0, f"dom.js XSS helper check failed (code {run_dom.returncode}): {run_dom.stderr}"

