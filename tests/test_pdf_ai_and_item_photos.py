import io
from pathlib import Path
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from app.schemas.item import DetectedItem, VisionAnalysisResponse
from app.services.vision_service import vision_service


def _make_text_pdf_bytes(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_image_only_pdf_bytes() -> bytes:
    img_buf = io.BytesIO()
    Image.new("RGB", (120, 90), color=(40, 120, 210)).save(img_buf, format="JPEG")
    img_buf.seek(0)

    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=letter)
    c.drawImage(ImageReader(img_buf), 72, 500, width=200, height=150)
    c.showPage()
    c.save()
    return pdf_buf.getvalue()


def _make_png_bytes(color=(10, 140, 220)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_analyze_pdf_extracts_items_and_returns_source_attachment(client, monkeypatch):
    loc = client.post("/api/v1/locations", json={"name": "B15", "location_type": "bin"}).json()

    captured = {}

    async def fake_analyze_text(text, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return VisionAnalysisResponse(
            status="success",
            items=[
                DetectedItem(
                    name="IR Break-Beam Sensor 3mm LEDs",
                    part_number="ADA-2167",
                    category="Sensors",
                    estimated_qty=2,
                    brief_specs="3.3V-5V DC, 25cm range, NPN open-collector output",
                )
            ],
            has_vision_api=True,
            provider_used="local",
            model_used="test-model",
            message="Detected 1 item from PDF.",
        )

    monkeypatch.setattr(vision_service, "_analyze_pdf_text", fake_analyze_text)

    pdf_bytes = _make_text_pdf_bytes(
        [
            "Adafruit IR Break Beam Sensor - 3mm LEDs",
            "Product ID: ADA-2167",
            "Supply Voltage: 3.3V to 5.5V DC",
            "Sensing Distance: up to 25cm (10 inches)",
            "Output: NPN open-collector, 100mA max",
        ]
    )

    resp = client.post(
        f"/api/v1/locations/{loc['id']}/analyze-pdf",
        files={"file": ("break-beam-datasheet.pdf", pdf_bytes, "application/pdf")},
        data={"provider": "local"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "IR Break-Beam Sensor 3mm LEDs"
    assert body["items"][0]["part_number"] == "ADA-2167"
    assert body["items"][0]["estimated_qty"] == 2
    assert "ADA-2167" in captured["text"]

    assert body["source_attachment_path"].startswith("/uploads/attachments/")
    assert body["source_attachment_path"].endswith(".pdf")
    assert body["source_attachment_filename"] == "break-beam-datasheet.pdf"
    assert body["source_attachment_size_bytes"] == len(pdf_bytes)


def test_analyze_pdf_rejects_non_pdf_file(client):
    loc = client.post("/api/v1/locations", json={"name": "B16", "location_type": "bin"}).json()
    resp = client.post(
        f"/api/v1/locations/{loc['id']}/analyze-pdf",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert resp.status_code == 400


def test_analyze_scanned_image_only_pdf_falls_back_to_vision(client, monkeypatch):
    loc = client.post("/api/v1/locations", json={"name": "B17", "location_type": "bin"}).json()

    called_with_paths = []

    async def fake_analyze_images(image_paths, **kwargs):
        called_with_paths.extend(image_paths)
        return VisionAnalysisResponse(
            status="success",
            items=[DetectedItem(name="Scanned Datasheet Component", estimated_qty=1)],
            has_vision_api=True,
            provider_used="local",
            model_used="test-vision",
            message="Detected 1 item from scanned PDF.",
        )

    monkeypatch.setattr(vision_service, "analyze_images", fake_analyze_images)

    pdf_bytes = _make_image_only_pdf_bytes()
    resp = client.post(
        f"/api/v1/locations/{loc['id']}/analyze-pdf",
        files={"file": ("scanned-sheet.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "Scanned Datasheet Component"
    assert len(called_with_paths) >= 1


def test_bulk_create_links_source_pdf_attachment_to_created_items(client):
    loc = client.post("/api/v1/locations", json={"name": "B18", "location_type": "bin"}).json()

    # Upload a PDF first via analyze-pdf (mocked fallback is fine since attachment is saved first)
    pdf_bytes = _make_text_pdf_bytes(["Sensor Datasheet Model X1"])
    item_tmp = client.post("/api/v1/items", json={"name": "Temp", "location_id": loc["id"]}).json()
    att = client.post(
        f"/api/v1/items/{item_tmp['id']}/attachments",
        files={"file": ("datasheet.pdf", pdf_bytes, "application/pdf")},
    ).json()

    bulk_resp = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": loc["id"],
            "save_mode": "separate",
            "source_attachment_path": att["file_path"],
            "source_attachment_filename": "datasheet.pdf",
            "source_attachment_title": "Sensor Datasheet",
            "items": [
                {"name": "Break-Beam Emitter", "quantity": 1, "location_id": loc["id"]},
                {"name": "Break-Beam Receiver", "quantity": 1, "location_id": loc["id"]},
            ],
        },
    )
    assert bulk_resp.status_code == 201, bulk_resp.text
    created = bulk_resp.json()
    assert len(created) == 2

    for item in created:
        atts = client.get(f"/api/v1/items/{item['id']}/attachments").json()
        assert len(atts) == 1
        assert atts[0]["file_path"] == att["file_path"]
        assert atts[0]["attachment_type"] == "pdf"
        assert atts[0]["title"] == "Sensor Datasheet"


def test_attach_uploaded_photo_to_item_and_main_image_behavior(client):
    loc = client.post("/api/v1/locations", json={"name": "B19", "location_type": "bin"}).json()
    item = client.post("/api/v1/items", json={"name": "IR Sensor", "location_id": loc["id"]}).json()
    assert item["item_photo"] is None

    # Upload first photo (simulates paste or phone upload)
    up1 = client.post(
        "/api/v1/photos/upload",
        files={"file": ("screenshot.png", _make_png_bytes((20, 180, 90)), "image/png")},
    )
    assert up1.status_code == 200
    p1 = up1.json()

    # Attach first photo to item — should also set item_photo since item has none yet
    a1 = client.post(
        f"/api/v1/items/{item['id']}/attachments/from-photo",
        json={
            "photo_path": p1["original_path"],
            "thumbnail_path": p1["thumbnail_path"],
            "title": "Main screenshot",
        },
    )
    assert a1.status_code == 201, a1.text
    att1 = a1.json()
    assert att1["attachment_type"] == "image"
    assert att1["file_path"] == p1["original_path"]
    assert att1["thumbnail_path"] == p1["thumbnail_path"]

    detail1 = client.get(f"/api/v1/items/{item['id']}").json()
    assert detail1["item_photo"] == p1["original_path"]
    assert len(detail1["attachments"]) == 1

    # Upload second photo (additional photo from phone/file/paste)
    up2 = client.post(
        "/api/v1/photos/upload",
        files={"file": ("angle2.png", _make_png_bytes((200, 50, 50)), "image/png")},
    )
    p2 = up2.json()

    a2 = client.post(
        f"/api/v1/items/{item['id']}/attachments/from-photo",
        json={
            "photo_path": p2["original_path"],
            "thumbnail_path": p2["thumbnail_path"],
            "set_as_main": False,
        },
    )
    assert a2.status_code == 201, a2.text

    # Main photo stays p1, but item now has 2 image attachments
    detail2 = client.get(f"/api/v1/items/{item['id']}").json()
    assert detail2["item_photo"] == p1["original_path"]
    assert len(detail2["attachments"]) == 2

    # Attaching with set_as_main=True updates main photo
    a3 = client.post(
        f"/api/v1/items/{item['id']}/attachments/from-photo",
        json={
            "photo_path": p2["original_path"],
            "thumbnail_path": p2["thumbnail_path"],
            "set_as_main": True,
        },
    )
    assert a3.status_code == 201
    detail3 = client.get(f"/api/v1/items/{item['id']}").json()
    assert detail3["item_photo"] == p2["original_path"]

    # Rejects traversal paths
    bad = client.post(
        f"/api/v1/items/{item['id']}/attachments/from-photo",
        json={"photo_path": "/uploads/../../etc/passwd"},
    )
    assert bad.status_code in (400, 422)
