import io
from pathlib import Path
import tempfile
from unittest.mock import patch

from fastapi import status
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.core.config import settings
from app.core.exceptions import AppError
from app.services.scanner_service import scanner_service
from app.services.vision_service import vision_service


def _make_blank_pdf_bytes(num_pages: int = 1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(num_pages):
        c.showPage()
    c.save()
    return buf.getvalue()


def _make_doc_png_bytes() -> bytes:
    img = Image.new("RGB", (900, 1200), (248, 248, 248))
    draw = ImageDraw.Draw(img)
    draw.text((60, 80), "SPECIFICATION SHEET", fill=(10, 10, 10))
    draw.line([(60, 130), (840, 130)], fill=(20, 20, 20), width=3)
    draw.text((60, 180), "Input: 5V DC", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_scanner_list_scanners_handles_app_error_without_crash(monkeypatch):
    monkeypatch.setattr("app.services.scanner_service.platform.system", lambda: "Windows")

    def _raise_wia(*_args, **_kwargs):
        raise AppError(
            "WIA service disabled",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="scanner_unavailable",
        )

    monkeypatch.setattr(scanner_service, "_run_powershell_json", _raise_wia)
    result = scanner_service.list_scanners()
    assert result["available"] is False
    assert result["devices"] == []
    assert result["message"] == "WIA service disabled"


def test_vision_extract_pdf_does_not_leak_temp_dir_on_blank_pdf(tmp_path):
    pdf_file = tmp_path / "blank.pdf"
    pdf_file.write_bytes(_make_blank_pdf_bytes(1))

    before = set(Path(tempfile.gettempdir()).glob("wims_pdf_img_*"))
    text, imgs = vision_service._extract_pdf_text_and_images(pdf_file)
    after = set(Path(tempfile.gettempdir()).glob("wims_pdf_img_*"))

    assert text == ""
    assert imgs == []
    assert after == before


def test_create_typeset_cleans_intermediate_enhanced_files_and_caps_pdf_pages(client):
    loc = client.post("/api/v1/locations", json={"name": "Docs Bin", "location_type": "bin"}).json()
    item = client.post("/api/v1/items", json={"name": "Module X", "location_id": loc["id"]}).json()

    # 1. Image attachment with enhance_first=True must not leave intermediate enhanced files behind
    img_att = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("sheet.png", _make_doc_png_bytes(), "image/png")},
    ).json()

    originals_before = {p.name for p in settings.ORIGINALS_DIR.iterdir()}
    ts_res = client.post(
        f"/api/v1/items/{item['id']}/attachments/{img_att['id']}/typeset",
        json={"typeset_mode": "clean_reflow", "enhance_first": True},
    )
    assert ts_res.status_code == 201, ts_res.text
    originals_after = {p.name for p in settings.ORIGINALS_DIR.iterdir()}
    assert originals_after == originals_before

    # 2. PDF attachment exceeding 50 pages must be rejected with 400
    huge_pdf = _make_blank_pdf_bytes(51)
    pdf_att = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("huge.pdf", huge_pdf, "application/pdf")},
    ).json()
    too_long = client.post(
        f"/api/v1/items/{item['id']}/attachments/{pdf_att['id']}/typeset",
        json={"typeset_mode": "layout_matched", "enhance_first": False},
    )
    assert too_long.status_code == 400
    assert "50" in too_long.text


def test_save_document_auto_enhance_cleans_intermediate_files(client):
    loc = client.post("/api/v1/locations", json={"name": "Manuals", "location_type": "bin"}).json()
    item = client.post("/api/v1/items", json={"name": "Meter", "location_id": loc["id"]}).json()

    up = client.post(
        "/api/v1/photos/upload",
        files={"file": ("page1.png", _make_doc_png_bytes(), "image/png")},
    ).json()
    src_path = up["original_path"]

    originals_before = {p.name for p in settings.ORIGINALS_DIR.iterdir()}
    save_res = client.post(
        f"/api/v1/items/{item['id']}/documents",
        json={
            "title": "Meter Quickstart",
            "pages": [{"source_path": src_path}],
            "save_enhanced_pdf": True,
            "save_typeset_pdf": False,
            "auto_enhance_unprocessed": True,
        },
    )
    assert save_res.status_code == 201, save_res.text
    originals_after = {p.name for p in settings.ORIGINALS_DIR.iterdir()}
    assert originals_after == originals_before


def test_rotate_attachment_rolls_back_cleanly_when_thumbnail_fails(client):
    loc = client.post("/api/v1/locations", json={"name": "Rot Bin", "location_type": "bin"}).json()
    item = client.post("/api/v1/items", json={"name": "Sensor", "location_id": loc["id"]}).json()

    att = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("diagram.png", _make_doc_png_bytes(), "image/png")},
    ).json()
    disk_file = settings.ORIGINALS_DIR / Path(att["file_path"]).name
    bytes_before = disk_file.read_bytes()

    original_save = Image.Image.save

    def failing_thumb_save(self, fp, *args, **kwargs):
        if ".rot.tmp" in str(fp) and str(kwargs.get("format", "")).upper() == "WEBP":
            raise OSError("Simulated thumbnail write failure")
        return original_save(self, fp, *args, **kwargs)

    with patch.object(Image.Image, "save", failing_thumb_save):
        res = client.post(
            f"/api/v1/items/{item['id']}/attachments/{att['id']}/rotate",
            json={"rotation_deg": 90},
        )

    assert res.status_code == 400
    assert disk_file.read_bytes() == bytes_before
    assert not list(settings.ORIGINALS_DIR.glob("*.rot.tmp"))
    assert not list(settings.THUMBNAILS_DIR.glob("*.rot.tmp"))
