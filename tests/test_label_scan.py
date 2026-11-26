from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw, ImageFilter
import pytest
from sqlalchemy import inspect

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.models.audit import AuditLog
from app.models.item import Item
from app.models.session import LabelScanSessionRecord
from app.schemas.label_scan import CellLabelResult
from app.services.label_scan_service import (
    _resolve_cell_outcome,
    assess_cell_quality,
    cluster_closeup_zones,
    label_scan_service,
    rectify_quad,
)


def _make_sticker_image(width: int = 320, height: int = 160, text_lines: list[str] | None = None, blur: float = 0.0) -> Image.Image:
    img = Image.new("RGB", (width, height), (28, 32, 40))
    draw = ImageDraw.Draw(img)
    pad_x, pad_y = max(10, width // 10), max(8, height // 6)
    draw.rectangle([pad_x, pad_y, width - pad_x, height - pad_y], fill=(245, 246, 248))
    lines = text_lines or ["LM2596", "DC-DC Buck"]
    y = pad_y + 10
    for ln in lines:
        # Draw high-contrast horizontal bars + text strokes so edge variance is realistic
        draw.text((pad_x + 12, y), ln, fill=(15, 20, 28))
        draw.rectangle([pad_x + 12, y + 14, min(width - pad_x - 12, pad_x + 12 + len(ln) * 9), y + 18], fill=(20, 24, 32))
        y += 24
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur))
    return img


def _img_to_jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_migration_009_label_scan_sessions_idempotent(db_session):
    from app.db.migrations import get_current_schema_version, run_migrations

    engine = db_session.get_bind()
    v1 = run_migrations(engine)
    v2 = run_migrations(engine)
    assert v1 == v2
    assert v1 >= 9
    assert get_current_schema_version(engine) == v1

    insp = inspect(engine)
    assert "label_scan_sessions" in insp.get_table_names()
    idx_names = {idx["name"] for idx in insp.get_indexes("label_scan_sessions")}
    assert "ix_label_scan_sessions_expires_at" in idx_names
    assert "ix_label_scan_sessions_container_id" in idx_names


def test_perspective_rectify_and_quality_gate():
    src = Image.new("RGB", (600, 400), (20, 20, 20))
    rectified = rectify_quad(src, [(0.1, 0.1), (0.9, 0.12), (0.88, 0.9), (0.12, 0.88)], 420, 280)
    assert rectified.size == (420, 280)

    # Collinear / collapsed quad must raise BadRequestError
    with pytest.raises(BadRequestError):
        rectify_quad(src, [(0.1, 0.1), (0.5, 0.1), (0.9, 0.1), (0.2, 0.1)], 300, 200)

    # Sharp printed sticker passes quality gate
    sharp = _make_sticker_image(300, 150)
    ok_sharp, has_sticker_sharp, q_sharp, reason_sharp = assess_cell_quality(sharp)
    assert has_sticker_sharp is True
    assert ok_sharp is True
    assert q_sharp >= 0.4
    assert reason_sharp is None

    # Heavily blurred sticker fails quality gate
    blurry = _make_sticker_image(300, 150, blur=5.0)
    ok_blur, has_sticker_blur, _, reason_blur = assess_cell_quality(blurry)
    assert has_sticker_blur is True
    assert ok_blur is False
    assert reason_blur and "blurry" in reason_blur.lower()

    # Too-small crop fails size gate
    tiny = _make_sticker_image(48, 28)
    ok_tiny, has_sticker_tiny, _, reason_tiny = assess_cell_quality(tiny)
    assert has_sticker_tiny is True
    assert ok_tiny is False
    assert reason_tiny and "small" in reason_tiny.lower()

    # Dark blank compartment has no sticker
    blank = Image.new("RGB", (240, 140), (24, 27, 32))
    ok_blank, has_sticker_blank, _, _ = assess_cell_quality(blank)
    assert ok_blank is False
    assert has_sticker_blank is False


def test_resolve_cell_outcome_and_clustering():
    # High-confidence OCR on sharp crop -> green
    name, desc, _, conf, tier, reason, src = _resolve_cell_outcome(
        is_usable=True,
        has_sticker=True,
        quality_score=0.85,
        quality_reason=None,
        crop_width=240,
        ocr_result=("LM2596\nDC-DC Step Down\n3A Adjustable", 0.94),
        vlm_entry=None,
    )
    assert tier == "green"
    assert conf == 94
    assert name == "LM2596 DC-DC Step Down"
    assert desc and "3A Adjustable" in desc
    assert reason is None
    assert src == "ocr"

    # Moderate OCR confidence -> yellow
    _, _, _, conf_y, tier_y, _, _ = _resolve_cell_outcome(
        is_usable=True,
        has_sticker=True,
        quality_score=0.7,
        quality_reason=None,
        crop_width=200,
        ocr_result=("HC-SR04", 0.71),
        vlm_entry=None,
    )
    assert tier_y == "yellow"
    assert conf_y == 71

    # OCR vs AI digit disagreement -> never green, downgraded to yellow
    _, _, _, conf_dis, tier_dis, reason_dis, _ = _resolve_cell_outcome(
        is_usable=True,
        has_sticker=True,
        quality_score=0.9,
        quality_reason=None,
        crop_width=220,
        ocr_result=("LM2596", 0.92),
        vlm_entry={"status": "confident", "proposed_name": "LM2576", "raw_text": "LM2576"},
    )
    assert tier_dis == "yellow"
    assert conf_dis <= 74
    assert reason_dis and "disagree" in reason_dis.lower()

    # Failed quality gate -> red with empty proposed_name (never guessed)
    name_r, _, _, _, tier_r, _, _ = _resolve_cell_outcome(
        is_usable=False,
        has_sticker=True,
        quality_score=0.2,
        quality_reason="Label is too blurry — close-up needed",
        crop_width=200,
        ocr_result=("HC-SR04", 0.95),
        vlm_entry={"status": "confident", "proposed_name": "HC-SR04"},
    )
    assert tier_r == "red"
    assert name_r == ""

    # Close-up zone clustering bounds neighboring red cells
    red_cells = [
        CellLabelResult(coord="A1", grid_row=1, grid_col=1, confidence=20, confidence_tier="red"),
        CellLabelResult(coord="B1", grid_row=1, grid_col=2, confidence=20, confidence_tier="red"),
        CellLabelResult(coord="A2", grid_row=2, grid_col=1, confidence=20, confidence_tier="red"),
        CellLabelResult(coord="H8", grid_row=8, grid_col=8, confidence=20, confidence_tier="red"),
    ]
    zones = cluster_closeup_zones(red_cells, grid_rows=8, grid_cols=8, max_rows=3, max_cols=4)
    assert len(zones) == 2
    assert zones[0].label == "A1\u2013B2"
    assert zones[0].slot_count == 3
    assert zones[1].label == "H8"


def test_label_scan_end_to_end_and_closeup_flow(client, db_session):
    room = client.post("/api/v1/locations", json={"name": "Electronics Lab", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Yellow Organizer", "location_type": "container", "parent_id": room["id"]},
    ).json()

    # Create a 2x3 grid with 1 custom-named slot (A1) and 4 generic slots, leaving C2 uncreated
    grid_res = client.put(
        f"/api/v1/locations/{cabinet['id']}/grid",
        json={
            "grid_rows": 2,
            "grid_cols": 3,
            "slots": [
                {"name": "My Custom Resistors", "location_type": "drawer", "grid_row": 1, "grid_col": 1},
                {"name": "B1", "location_type": "drawer", "grid_row": 1, "grid_col": 2},
                {"name": "Drawer 3", "location_type": "drawer", "grid_row": 1, "grid_col": 3},
                {"name": "A2", "location_type": "drawer", "grid_row": 2, "grid_col": 1},
                {"name": "B2", "location_type": "drawer", "grid_row": 2, "grid_col": 2},
            ],
        },
    )
    assert grid_res.status_code == 200

    # 1. Start session + fetch QR PNG
    sess_resp = client.post("/api/v1/label-scan/sessions", json={"container_id": cabinet["id"]})
    assert sess_resp.status_code == 201
    sess = sess_resp.json()
    token = sess["token"]
    assert sess["qr_url"].endswith(f"/api/v1/label-scan/sessions/{token}/qr")

    qr_resp = client.get(sess["qr_url"])
    assert qr_resp.status_code == 200
    assert qr_resp.headers["content-type"] == "image/png"
    assert qr_resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    # 2. Upload high-res 2400x1600 overview photo (>1600px normal cap must be preserved!)
    overview = Image.new("RGB", (2400, 1600), (22, 24, 30))
    cell_w, cell_h = 800, 800
    for r in range(2):
        for c in range(3):
            if (r, c) == (1, 2):
                continue  # leave C2 blank/unlabeled
            blur = 6.0 if r == 1 else 0.0
            sticker = _make_sticker_image(cell_w, cell_h, [f"Part R{r+1}C{c+1}", "5V Module"], blur=blur)
            overview.paste(sticker, (c * cell_w, r * cell_h))

    up_resp = client.post(
        f"/api/v1/label-scan/sessions/{token}/upload",
        files={"file": ("overview.jpg", _img_to_jpeg_bytes(overview), "image/jpeg")},
    )
    assert up_resp.status_code == 200
    overview_path = up_resp.json()["original_path"]
    saved_disk = settings.ORIGINALS_DIR / up_resp.json()["filename"]
    with Image.open(saved_disk) as saved_img:
        assert saved_img.width == 2400 and saved_img.height == 1600

    # 3. Analyze overview with deterministic RapidOCR mock + mocked VLM
    ocr_calls = {"count": 0}

    def fake_ocr(crop):
        idx = ocr_calls["count"]
        ocr_calls["count"] += 1
        # A1, B1 high conf; C1 moderate conf (yellow)
        if idx == 0:
            return ("Arduino UNO", 0.95)
        if idx == 1:
            return ("ESP32-C3\nWiFi BLE", 0.93)
        return ("HC-SR04P", 0.72)

    with patch("app.services.label_scan_service._get_rapidocr", return_value=object()), patch(
        "app.services.label_scan_service._run_rapidocr_on_crop", side_effect=fake_ocr
    ), patch(
        "app.services.label_scan_service._call_vision_label_batch", new=AsyncMock(return_value=({}, "none"))
    ):
        an_resp = client.post(
            f"/api/v1/label-scan/sessions/{token}/analyze",
            json={
                "photo_path": overview_path,
                "quad": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}],
                "use_ollama_refinement": False,
            },
        )

    assert an_resp.status_code == 200
    an_data = an_resp.json()
    assert an_data["summary"] == {"total": 6, "green": 2, "yellow": 1, "red": 2, "unlabeled": 1}
    by_coord = {r["coord"]: r for r in an_data["results"]}
    assert by_coord["A1"]["has_custom_existing_name"] is True
    assert by_coord["B1"]["confidence_tier"] == "green"
    assert by_coord["C1"]["confidence_tier"] == "yellow"
    assert by_coord["A2"]["confidence_tier"] == "red" and by_coord["A2"]["proposed_name"] == ""
    assert by_coord["B2"]["confidence_tier"] == "red" and by_coord["B2"]["proposed_name"] == ""
    assert by_coord["C2"]["confidence_tier"] == "unlabeled"
    assert len(an_data["closeup_zones"]) == 1
    zone = an_data["closeup_zones"][0]
    assert zone["label"] == "A2\u2013B2"

    # 4. Request close-up on phone + upload crisp close-up photo
    req_cu = client.post(
        f"/api/v1/label-scan/sessions/{token}/request-closeup",
        json={"zone_id": zone["zone_id"]},
    )
    assert req_cu.status_code == 200
    assert req_cu.json()["status"] == "awaiting_closeup"
    assert req_cu.json()["active_zone"]["zone_id"] == zone["zone_id"]

    closeup = Image.new("RGB", (1200, 600), (22, 24, 30))
    closeup.paste(_make_sticker_image(600, 600, ["LM2596"]), (0, 0))
    closeup.paste(_make_sticker_image(600, 600, ["XT60 Conn"]), (600, 0))
    cu_up = client.post(
        f"/api/v1/label-scan/sessions/{token}/upload",
        files={"file": ("closeup.jpg", _img_to_jpeg_bytes(closeup), "image/jpeg")},
        data={"zone_id": zone["zone_id"]},
    )
    assert cu_up.status_code == 200
    cu_path = cu_up.json()["original_path"]

    cu_iter = iter([("LM2596", 0.96), ("XT60 Conn", 0.94)])
    with patch("app.services.label_scan_service._get_rapidocr", return_value=object()), patch(
        "app.services.label_scan_service._run_rapidocr_on_crop", side_effect=lambda _c: next(cu_iter)
    ), patch(
        "app.services.label_scan_service._call_vision_label_batch", new=AsyncMock(return_value=({}, "none"))
    ):
        cu_an = client.post(
            f"/api/v1/label-scan/sessions/{token}/analyze",
            json={
                "photo_path": cu_path,
                "quad": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}],
                "zone_id": zone["zone_id"],
                "min_row": zone["min_row"],
                "max_row": zone["max_row"],
                "min_col": zone["min_col"],
                "max_col": zone["max_col"],
                "use_ollama_refinement": False,
            },
        )

    assert cu_an.status_code == 200
    cu_data = cu_an.json()
    assert cu_data["summary"] == {"total": 6, "green": 4, "yellow": 1, "red": 0, "unlabeled": 1}
    assert cu_data["closeup_zones"] == []

    # 5. Apply labels (protect custom A1 name, update B1/C1/A2/B2, create starter items)
    merged_by_coord = {r["coord"]: r for r in cu_data["results"]}
    updates = [
        {
            "slot_id": merged_by_coord[coord]["slot_id"],
            "grid_row": merged_by_coord[coord]["grid_row"],
            "grid_col": merged_by_coord[coord]["grid_col"],
            "name": merged_by_coord[coord]["proposed_name"],
            "description": merged_by_coord[coord]["proposed_description"],
            "create_starter_item": True,
        }
        for coord in ("A1", "B1", "C1", "A2", "B2")
    ]
    apply_resp = client.post(
        f"/api/v1/label-scan/sessions/{token}/apply",
        json={"updates": updates, "overwrite_existing": False},
    )
    assert apply_resp.status_code == 200
    apply_data = apply_resp.json()
    assert apply_data["updated_count"] == 4
    assert apply_data["skipped_existing_count"] == 1
    assert apply_data["created_items_count"] == 4

    # Verify A1 kept its custom name, while B1 & A2 updated + starter items + audit logs written
    a1_loc = client.get(f"/api/v1/locations/{merged_by_coord['A1']['slot_id']}").json()
    b1_loc = client.get(f"/api/v1/locations/{merged_by_coord['B1']['slot_id']}").json()
    a2_loc = client.get(f"/api/v1/locations/{merged_by_coord['A2']['slot_id']}").json()
    assert a1_loc["name"] == "My Custom Resistors"
    assert b1_loc["name"] == "ESP32-C3 WiFi BLE"
    assert a2_loc["name"] == "LM2596"
    assert db_session.query(Item).count() == 4
    assert db_session.query(AuditLog).count() == 4


def test_label_scan_security_auth_idor_and_expiry(client, db_session, monkeypatch):
    c1 = client.post("/api/v1/locations", json={"name": "Cabinet One", "location_type": "container"}).json()
    c2 = client.post("/api/v1/locations", json={"name": "Cabinet Two", "location_type": "container"}).json()
    c2_slot = client.post(
        "/api/v1/locations",
        json={"name": "Other Slot", "location_type": "drawer", "parent_id": c2["id"]},
    ).json()

    s1 = client.post("/api/v1/label-scan/sessions", json={"container_id": c1["id"]}).json()
    s2 = client.post("/api/v1/label-scan/sessions", json={"container_id": c2["id"]}).json()
    t1, t2 = s1["token"], s2["token"]

    # Upload a valid photo to s2 so we can test cross-session IDOR on s1
    up_s2 = client.post(
        f"/api/v1/label-scan/sessions/{t2}/upload",
        files={"file": ("s2.jpg", _img_to_jpeg_bytes(_make_sticker_image()), "image/jpeg")},
    ).json()

    # IDOR 1: cannot analyze s2's photo using s1's token
    idor_photo = client.post(
        f"/api/v1/label-scan/sessions/{t1}/analyze",
        json={
            "photo_path": up_s2["original_path"],
            "quad": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}],
            "grid_rows": 2,
            "grid_cols": 2,
        },
    )
    assert idor_photo.status_code == 400

    # IDOR 2: cannot apply labels to a slot belonging to another container
    idor_slot = client.post(
        f"/api/v1/label-scan/sessions/{t1}/apply",
        json={"updates": [{"slot_id": c2_slot["id"], "name": "Hacked"}]},
    )
    assert idor_slot.status_code == 400

    # PIN auth enforcement: desktop endpoints require auth while QR-scoped phone endpoints remain reachable
    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", "4321")
    monkeypatch.setattr(settings, "WIMS_AUTH_BYPASS_LOCALHOST", False)

    assert client.post("/api/v1/label-scan/sessions", json={"container_id": c1["id"]}).status_code == 401
    assert client.post(f"/api/v1/label-scan/sessions/{t1}/request-closeup", json={"clear": True}).status_code == 401
    assert client.post(f"/api/v1/label-scan/sessions/{t1}/apply", json={"updates": []}).status_code == 401

    assert client.get(f"/api/v1/label-scan/sessions/{t1}").status_code == 200
    assert client.get(f"/api/v1/label-scan/sessions/{t1}/qr").status_code == 200
    assert (
        client.post(
            f"/api/v1/label-scan/sessions/{t1}/upload",
            files={"file": ("phone.jpg", _img_to_jpeg_bytes(_make_sticker_image()), "image/jpeg")},
        ).status_code
        == 200
    )

    # Expired session returns 410 Gone
    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", None)
    rec = db_session.get(LabelScanSessionRecord, t1)
    rec.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    db_session.commit()
    assert (
        client.post(
            f"/api/v1/label-scan/sessions/{t1}/upload",
            files={"file": ("late.jpg", _img_to_jpeg_bytes(_make_sticker_image()), "image/jpeg")},
        ).status_code
        == 410
    )

    # Max active sessions limit returns 429
    monkeypatch.setattr(label_scan_service, "MAX_SESSIONS", 1)
    cap_resp = client.post("/api/v1/label-scan/sessions", json={"container_id": c1["id"]})
    assert cap_resp.status_code == 429


def test_label_scan_frontend_csp_syntax_and_sw(client):
    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    js_dir = static_dir / "js"

    # Strict no-inline-script check on label-capture.html and index.html
    inline_script_re = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
    for html_name in ("label-capture.html", "index.html"):
        html = (static_dir / html_name).read_text(encoding="utf-8")
        assert not inline_script_re.findall(html), f"Inline script found in {html_name}"

    assert 'id="label-scan-modal"' in (static_dir / "index.html").read_text(encoding="utf-8")
    assert "openLabelScanModal(" in (js_dir / "views.js").read_text(encoding="utf-8")

    # CSP header on /label-capture.html matches strict capture.html policy
    resp = client.get("/label-capture.html")
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]

    # Service worker caches new assets
    sw_src = (static_dir / "sw.js").read_text(encoding="utf-8")
    for asset in ("/label-capture.html", "/js/label-capture-page.js", "/js/label-scan.js"):
        assert asset in sw_src
        assert (static_dir / asset.lstrip("/")).exists()

    node_exe = shutil.which("node")
    if node_exe:
        for js_name in ("label-scan.js", "label-capture-page.js", "views.js", "app.js"):
            proc = subprocess.run([node_exe, "--check", str(js_dir / js_name)], capture_output=True, text=True)
            assert proc.returncode == 0, f"Syntax error in {js_name}:\n{proc.stderr}"


def test_label_scan_non_uniform_row_boundaries(client):
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Mixed Height Cabinet", "location_type": "container"},
    ).json()
    client.put(
        f"/api/v1/locations/{cabinet['id']}/grid",
        json={
            "grid_rows": 2,
            "grid_cols": 1,
            "slots": [
                {"name": "A1", "location_type": "drawer", "grid_row": 1, "grid_col": 1},
                {"name": "A2", "location_type": "drawer", "grid_row": 2, "grid_col": 1},
            ],
        },
    )
    sess = client.post("/api/v1/label-scan/sessions", json={"container_id": cabinet["id"]}).json()
    token = sess["token"]

    # Row 1 is 25% tall, Row 2 is 75% tall
    img = Image.new("RGB", (600, 800), (22, 24, 30))
    img.paste(_make_sticker_image(600, 200, ["Top Short"]), (0, 0))
    img.paste(_make_sticker_image(600, 600, ["Bottom Tall"]), (0, 200))
    up = client.post(
        f"/api/v1/label-scan/sessions/{token}/upload",
        files={"file": ("mixed.jpg", _img_to_jpeg_bytes(img), "image/jpeg")},
    ).json()

    # Invalid non-monotonic row_boundaries must be rejected with 400
    bad = client.post(
        f"/api/v1/label-scan/sessions/{token}/analyze",
        json={
            "photo_path": up["original_path"],
            "quad": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}],
            "row_boundaries": [0.0, 0.8, 0.5],
        },
    )
    assert bad.status_code == 400

    # Valid custom row_boundaries [0.0, 0.25, 1.0] + col_boundaries
    crop_heights: list[int] = []

    def capture_ocr(crop):
        crop_heights.append(crop.height)
        return ("Top Short" if len(crop_heights) == 1 else "Bottom Tall", 0.95)

    with patch("app.services.label_scan_service._get_rapidocr", return_value=object()), patch(
        "app.services.label_scan_service._run_rapidocr_on_crop", side_effect=capture_ocr
    ), patch(
        "app.services.label_scan_service._call_vision_label_batch", new=AsyncMock(return_value=({}, "none"))
    ):
        ok = client.post(
            f"/api/v1/label-scan/sessions/{token}/analyze",
            json={
                "photo_path": up["original_path"],
                "quad": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}],
                "row_boundaries": [0.0, 0.25, 1.0],
                "col_boundaries": [0.0, 1.0],
                "use_ollama_refinement": False,
            },
        )
    assert ok.status_code == 200
    assert len(crop_heights) == 2
    assert crop_heights[1] > crop_heights[0] * 2

    # Invalid col_boundaries length must be rejected with 400
    bad_col = client.post(
        f"/api/v1/label-scan/sessions/{token}/analyze",
        json={
            "photo_path": up["original_path"],
            "quad": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}],
            "col_boundaries": [0.0, 0.5, 1.0],
        },
    )
    assert bad_col.status_code == 400

    # Fail fast with 503 when neither RapidOCR nor Ollama/vision is reachable (never fake 100% red close-ups)
    with patch("app.services.label_scan_service._get_rapidocr", return_value=None), patch(
        "app.services.label_scan_service._call_vision_label_batch",
        new=AsyncMock(return_value=({}, "connection_failed")),
    ) as mock_vlm:
        unavail = client.post(
            f"/api/v1/label-scan/sessions/{token}/analyze",
            json={
                "photo_path": up["original_path"],
                "quad": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}],
                "row_boundaries": [0.0, 0.25, 1.0],
            },
        )
    assert unavail.status_code == 503
    assert mock_vlm.await_count == 1
    assert "Ollama" in unavail.text
