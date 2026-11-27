import io
from pathlib import Path
import shutil
import subprocess
from PIL import Image, ImageDraw
from pypdf import PdfReader
from app.services.scanner_service import scanner_service


def _make_doc_photo_bytes() -> bytes:
    img = Image.new("RGB", (1100, 1400), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    draw.text((80, 90), "PARTS MANUAL REV B", fill=(15, 15, 15))
    draw.line([(80, 140), (1020, 140)], fill=(30, 30, 30), width=4)
    draw.text((80, 210), "Supply Voltage: 12V DC 2A", fill=(20, 20, 20))
    draw.text((80, 270), "Connector: JST-XH 4-Pin", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_scanners_list_and_mocked_scan(client, monkeypatch):
    monkeypatch.setattr(
        scanner_service,
        "list_scanners",
        lambda: {
            "available": True,
            "platform": "Windows",
            "backend": "wia",
            "devices": [
                {
                    "id": "wia-test-1",
                    "name": "Test Flatbed Scanner",
                    "manufacturer": "TestCorp",
                    "description": "Flatbed",
                    "backend": "wia",
                }
            ],
            "message": None,
        },
    )
    res = client.get("/api/v1/documents/scanners")
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert len(data["devices"]) == 1
    assert data["devices"][0]["id"] == "wia-test-1"

    def _fake_scan_page(*, device_id=None, dpi=300, color_mode="color", source="flatbed"):
        assert device_id == "wia-test-1"
        assert dpi == 300
        return {
            "original_path": "/uploads/originals/scan_fake.png",
            "thumbnail_path": "/uploads/thumbnails/scan_fake_thumb.webp",
            "filename": "scan_fake.png",
            "width": 1100,
            "height": 1400,
            "scanner_name": "Test Flatbed Scanner",
        }

    monkeypatch.setattr(scanner_service, "scan_page", _fake_scan_page)
    scan_res = client.post(
        "/api/v1/documents/scan",
        json={"device_id": "wia-test-1", "dpi": 300, "color_mode": "color", "source": "flatbed"},
    )
    assert scan_res.status_code == 200
    assert scan_res.json()["scanner_name"] == "Test Flatbed Scanner"


def test_detect_corners_enhance_and_save_dual_pdfs(client):
    loc = client.post("/api/v1/locations", json={"name": "Bench", "location_type": "drawer"}).json()
    item = client.post(
        "/api/v1/items",
        json={"name": "24V Power Supply", "location_id": loc["id"]},
    ).json()

    up = client.post(
        "/api/v1/photos/upload?max_dimension=3200",
        files={"file": ("manual.png", _make_doc_photo_bytes(), "image/png")},
    )
    assert up.status_code == 200, up.text
    src_path = up.json()["original_path"]

    # Path traversal rejected
    bad_corners = client.post("/api/v1/documents/detect-corners", json={"photo_path": "/uploads/../secret.png"})
    assert bad_corners.status_code == 422

    corners_res = client.post("/api/v1/documents/detect-corners", json={"photo_path": src_path})
    assert corners_res.status_code == 200, corners_res.text
    corners = corners_res.json()
    assert len(corners["quad"]) == 4

    enh_res = client.post(
        "/api/v1/documents/enhance",
        json={
            "photo_path": src_path,
            "options": {"preset": "auto_document", "auto_perspective": True, "remove_shading": True},
        },
    )
    assert enh_res.status_code == 200, enh_res.text
    enh_data = enh_res.json()
    assert enh_data["enhanced_path"].startswith("/uploads/originals/")

    # Save both Enhanced Scan PDF and Typeset PDF to the item
    save_res = client.post(
        f"/api/v1/items/{item['id']}/documents",
        json={
            "title": "PSU Wiring Manual",
            "pages": [{"source_path": src_path, "enhanced_path": enh_data["enhanced_path"]}],
            "save_enhanced_pdf": True,
            "save_typeset_pdf": True,
            "typeset_mode": "layout_matched",
            "include_graphics": True,
        },
    )
    assert save_res.status_code == 201, save_res.text
    saved = save_res.json()
    assert saved["page_count"] == 1
    assert saved["enhanced_pdf"] is not None
    assert saved["typeset_pdf"] is not None
    assert saved["ocr_available"] is True
    assert saved["ocr_word_count"] >= 3

    # Verify both PDFs appear in item attachments and second PDF contains selectable vector text
    att_res = client.get(f"/api/v1/items/{item['id']}/attachments")
    assert att_res.status_code == 200
    attachments = att_res.json()
    assert len(attachments) == 2

    typeset_url = saved["typeset_pdf"]["file_path"]
    pdf_dl = client.get(typeset_url)
    assert pdf_dl.status_code == 200
    text = PdfReader(io.BytesIO(pdf_dl.content)).pages[0].extract_text() or ""
    assert any(tok in text.upper() for tok in ("MANUAL", "VOLTAGE", "CONNECTOR", "JST"))

    # Verify live Typeset PDF preview endpoint returns valid zoomable vector PDF
    prev_res = client.post(
        "/api/v1/documents/preview-typeset",
        json={"photo_path": enh_data["enhanced_path"], "typeset_mode": "layout_matched", "include_graphics": True},
    )
    assert prev_res.status_code == 200
    assert prev_res.headers["content-type"] == "application/pdf"
    assert int(prev_res.headers.get("x-wims-ocr-words", "0")) >= 3
    prev_text = PdfReader(io.BytesIO(prev_res.content)).pages[0].extract_text() or ""
    assert any(tok in prev_text.upper() for tok in ("MANUAL", "VOLTAGE", "CONNECTOR", "JST"))


def test_create_typeset_pdf_from_existing_attachment(client):
    loc = client.post("/api/v1/locations", json={"name": "Shelf A", "location_type": "shelf"}).json()
    item = client.post("/api/v1/items", json={"name": "Oscilloscope", "location_id": loc["id"]}).json()

    att_up = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("wiring.png", _make_doc_photo_bytes(), "image/png")},
        data={"title": "Wiring Diagram"},
    )
    assert att_up.status_code == 201
    att_id = att_up.json()["id"]

    ts_res = client.post(
        f"/api/v1/items/{item['id']}/attachments/{att_id}/typeset",
        json={"typeset_mode": "clean_reflow", "include_graphics": True, "enhance_first": True},
    )
    assert ts_res.status_code == 201, ts_res.text
    ts_data = ts_res.json()
    assert ts_data["typeset_pdf"] is not None
    assert "Typeset" in ts_data["typeset_pdf"]["title"]
    assert len(client.get(f"/api/v1/items/{item['id']}/attachments").json()) == 2


def test_high_res_qr_capture_session_and_csp_headers(client):
    sess = client.post(
        "/api/v1/photos/capture-sessions",
        json={"label": "Document: Meter", "high_res": True},
    )
    assert sess.status_code == 201
    sdata = sess.json()
    assert "hq=1" in sdata["capture_url"]
    assert "hq=1" in sdata["qr_url"]

    qr_res = client.get(sdata["qr_url"])
    assert qr_res.status_code == 200
    assert qr_res.headers["content-type"] == "image/png"

    home = client.get("/")
    assert home.status_code == 200
    assert "media-src 'self' blob: mediastream:" in home.headers.get("Content-Security-Policy", "")


def test_document_studio_js_registered_and_valid_syntax():
    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    sw_src = (static_dir / "sw.js").read_text(encoding="utf-8")
    assert "/js/document-studio.js" in sw_src

    node_exe = shutil.which("node")
    if node_exe:
        for js_rel in ("document-studio.js", "views.js", "app.js", "capture-page.js"):
            proc = subprocess.run([node_exe, "--check", str(static_dir / "js" / js_rel)], capture_output=True, text=True)
            assert proc.returncode == 0, f"Syntax error in {js_rel}:\n{proc.stderr}"
