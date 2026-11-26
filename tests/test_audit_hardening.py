import io
import logging
import socket
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
import pytest
from sqlalchemy import event, inspect

from app.core.config import settings


def _make_jpeg_bytes(color=(90, 140, 190), size=(240, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_vision_model_parameter_rejects_traversal_and_injection(client):
    drawer = client.post("/api/v1/locations", json={"name": "Model Guard Drawer", "location_type": "drawer"}).json()
    photo = client.post(
        f"/api/v1/locations/{drawer['id']}/photos",
        files={"file": ("guard.jpg", _make_jpeg_bytes(), "image/jpeg")},
    ).json()

    bad_models = [
        "../../projects/123",
        "gemini-2.5-flash?alt=media",
        "gemini-2.5-flash#frag",
        "gemini 2.5 flash",
        "gemini/../../secret",
        "bad:model",
    ]
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "httpx.AsyncClient.post", new=AsyncMock()
    ) as mock_post:
        for bad in bad_models:
            resp = client.post(
                f"/api/v1/locations/{drawer['id']}/analyze-photo",
                data={"photo_id": str(photo["id"]), "provider": "gemini", "model": bad},
            )
            assert resp.status_code == 400, f"Expected 400 for bad gemini model {bad!r}, got {resp.status_code}: {resp.text}"
        assert mock_post.call_count == 0

    # Valid models still work
    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.json.return_value = {"message": {"content": '{"items": []}'}}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_ok)):
        ok_local = client.post(
            f"/api/v1/locations/{drawer['id']}/analyze-photo",
            data={"photo_id": str(photo["id"]), "provider": "local", "model": "qwen2.5vl:7b"},
        )
        assert ok_local.status_code == 200
        assert ok_local.json()["status"] == "success"


def test_ssrf_dns_fails_closed_and_blocks_mapped_cgnat_and_credentials():
    from app.services.image_lookup_service import _host_resolves_publicly, _is_safe_public_https_url

    # Embedded credentials and non-global IP literals rejected
    assert _is_safe_public_https_url("https://user:pass@example.com/img.jpg") is False
    assert _is_safe_public_https_url("https://[::ffff:127.0.0.1]/img.jpg") is False
    assert _is_safe_public_https_url("https://100.64.0.1/img.jpg") is False
    assert _is_safe_public_https_url("https://0.0.0.0/img.jpg") is False
    assert _is_safe_public_https_url("https://93.184.216.34/img.jpg") is True

    # DNS failure must fail closed (False), not open (True)
    with patch("app.services.image_lookup_service.socket.getaddrinfo", side_effect=socket.gaierror("fail")):
        assert _host_resolves_publicly("nonexistent.example") is False

    # DNS returning empty list must fail closed
    with patch("app.services.image_lookup_service.socket.getaddrinfo", return_value=[]):
        assert _host_resolves_publicly("empty.example") is False

    # DNS returning IPv4-mapped IPv6 loopback or CGNAT must fail
    with patch(
        "app.services.image_lookup_service.socket.getaddrinfo",
        return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:127.0.0.1", 443, 0, 0))],
    ):
        assert _host_resolves_publicly("rebind.example") is False


def test_attachment_magic_bytes_zip_bomb_and_download_headers(client):
    loc = client.post("/api/v1/locations", json={"name": "Attachment Bin", "location_type": "drawer"}).json()
    item = client.post(
        "/api/v1/items",
        json={"name": "Oscilloscope", "location_id": loc["id"], "quantity": 1},
    ).json()

    # Spoofed PDF (HTML payload with .pdf extension)
    bad_pdf = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("evil.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")},
    )
    assert bad_pdf.status_code == 400

    # Spoofed ZIP
    bad_zip = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("fake.zip", b"not-a-zip-file", "application/zip")},
    )
    assert bad_zip.status_code == 400

    # Invalid JSON
    bad_json = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("broken.json", b"{not valid json", "application/json")},
    )
    assert bad_json.status_code == 400

    # Null byte in text file
    bad_txt = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("bin.txt", b"hello\x00world", "text/plain")},
    )
    assert bad_txt.status_code == 400

    # Zip bomb (uncompressed > 50MB)
    bomb_buf = io.BytesIO()
    with zipfile.ZipFile(bomb_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.txt", b"A" * (52 * 1024 * 1024))
    bomb_resp = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("bomb.zip", bomb_buf.getvalue(), "application/zip")},
    )
    assert bomb_resp.status_code == 400

    # Valid ZIP uploads cleanly and serves with Content-Disposition: attachment
    ok_buf = io.BytesIO()
    with zipfile.ZipFile(ok_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", b"calibration data")
    ok_zip = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("docs.zip", ok_buf.getvalue(), "application/zip")},
    )
    assert ok_zip.status_code == 201, ok_zip.text
    file_url = ok_zip.json()["file_path"]
    dl = client.get(file_url)
    assert dl.status_code == 200
    assert "attachment" in dl.headers.get("content-disposition", "").lower()


def test_qr_endpoint_rejects_unsafe_target_and_base_urls(client):
    loc = client.post("/api/v1/locations", json={"name": "QR Drawer", "location_type": "drawer"}).json()

    for bad in ("javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd", "https://user:pass@evil.com"):
        r1 = client.get(f"/api/v1/locations/{loc['id']}/qr", params={"target_url": bad})
        assert r1.status_code == 400, f"Expected 400 for target_url={bad!r}, got {r1.status_code}"
        r2 = client.get(f"/api/v1/locations/{loc['id']}/qr", params={"base_url": bad})
        assert r2.status_code == 400, f"Expected 400 for base_url={bad!r}, got {r2.status_code}"

    ok = client.get(f"/api/v1/locations/{loc['id']}/qr", params={"base_url": "http://workshop.local:8000"})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"


def test_scoped_snapshot_avoids_full_table_scan_on_single_entity(client, db_session):
    # Create an unrelated subtree of 8 locations
    room_other = client.post("/api/v1/locations", json={"name": "Unrelated Room", "location_type": "room"}).json()
    for i in range(7):
        client.post(
            "/api/v1/locations",
            json={"name": f"Other Bin {i}", "location_type": "bin", "parent_id": room_other["id"]},
        )

    target_room = client.post("/api/v1/locations", json={"name": "Target Room", "location_type": "room"}).json()
    target_bin = client.post(
        "/api/v1/locations",
        json={"name": "Target Bin", "location_type": "bin", "parent_id": target_room["id"]},
    ).json()
    item = client.post(
        "/api/v1/items",
        json={"name": "Scoped Resistor", "location_id": target_bin["id"], "quantity": 10},
    ).json()

    from app.services.location_service import location_service

    snap = location_service.build_scoped_snapshot(db_session, [target_bin["id"]], include_counts=False, include_photos=False)
    assert set(snap.loc_by_id.keys()) == {target_room["id"], target_bin["id"]}
    assert room_other["id"] not in snap.loc_by_id

    # Endpoint outputs remain identical
    loc_read = client.get(f"/api/v1/locations/{target_bin['id']}")
    assert loc_read.status_code == 200
    assert loc_read.json()["full_path"] == "Target Room > Target Bin"

    item_read = client.get(f"/api/v1/items/{item['id']}")
    assert item_read.status_code == 200
    assert item_read.json()["location_full_path"] == "Target Room > Target Bin"


def test_grid_partial_unique_index_and_swap_save_safety(client, db_session):
    inspector = inspect(db_session.get_bind())
    idx_names = {idx["name"] for idx in inspector.get_indexes("locations")}
    assert "uq_locations_parent_grid_pos" in idx_names

    unit = client.post(
        "/api/v1/locations",
        json={"name": "Grid Cabinet", "location_type": "container", "grid_rows": 1, "grid_cols": 2},
    ).json()
    slot_a = client.post(
        f"/api/v1/locations/{unit['id']}/grid/slot",
        json={"grid_row": 1, "grid_col": 1},
    ).json()
    slot_b = client.post(
        f"/api/v1/locations/{unit['id']}/grid/slot",
        json={"grid_row": 1, "grid_col": 2},
    ).json()

    # Swapping two slots must succeed even with the partial unique index active
    swap = client.post(
        f"/api/v1/locations/{unit['id']}/grid/move",
        json={"source_id": slot_a["id"], "target_id": slot_b["id"]},
    )
    assert swap.status_code == 200, swap.text

    # Reversing coordinates via save_grid_layout must also succeed cleanly
    save_resp = client.put(
        f"/api/v1/locations/{unit['id']}/grid",
        json={
            "grid_rows": 1,
            "grid_cols": 2,
            "slots": [
                {"id": slot_a["id"], "location_type": "drawer", "grid_row": 1, "grid_col": 1},
                {"id": slot_b["id"], "location_type": "drawer", "grid_row": 1, "grid_col": 2},
            ],
        },
    )
    assert save_resp.status_code == 200, save_resp.text


def test_session_services_enforce_max_active_sessions(client, monkeypatch):
    from app.services.capture_session_service import capture_session_service
    from app.services.unit_setup_service import unit_setup_service

    monkeypatch.setattr(capture_session_service, "MAX_SESSIONS", 2)
    assert client.post("/api/v1/photos/capture-sessions", json={"label": "S1"}).status_code == 201
    assert client.post("/api/v1/photos/capture-sessions", json={"label": "S2"}).status_code == 201
    r_cap = client.post("/api/v1/photos/capture-sessions", json={"label": "S3"})
    assert r_cap.status_code == 429

    monkeypatch.setattr(unit_setup_service, "MAX_SESSIONS", 2)
    assert client.post("/api/v1/unit-setup/sessions", json={}).status_code == 201
    assert client.post("/api/v1/unit-setup/sessions", json={}).status_code == 201
    r_unit = client.post("/api/v1/unit-setup/sessions", json={})
    assert r_unit.status_code == 429


def test_crop_many_and_save_opens_source_once(tmp_path):
    from app.services.photo_service import photo_service

    src = tmp_path / "batch_crop.jpg"
    Image.new("RGB", (400, 300), (40, 90, 140)).save(src, format="JPEG")

    with patch("app.services.photo_service.Image.open", wraps=Image.open) as spy_open:
        results = photo_service.crop_many_and_save(
            src,
            [[50, 50, 300, 300], [500, 500, 900, 900], None],
            coord_mode="norm_1000",
        )
    assert spy_open.call_count == 1
    assert results[0] and results[0].startswith("/uploads/originals/")
    assert results[1] and results[1].startswith("/uploads/originals/")
    assert results[2] is None


def test_health_returns_503_when_degraded_and_middleware_logs_status(client, caplog, monkeypatch):
    wims_logger = logging.getLogger("wims")
    wims_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="wims"):
            ok_resp = client.get("/api/v1/health", headers={"X-Request-ID": "mw-status-1"})
            assert ok_resp.status_code == 200
        assert any("GET /api/v1/health -> 200" in rec.message for rec in caplog.records)
    finally:
        wims_logger.removeHandler(caplog.handler)

    monkeypatch.setattr("app.api.system._check_uploads_writable", lambda: False)
    degraded = client.get("/api/v1/health")
    assert degraded.status_code == 503
    assert degraded.json()["status"] == "degraded"


def test_openapi_has_typed_response_schemas(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    expected = [
        ("/api/v1/items/{item_id}/find-image", "post"),
        ("/api/v1/locations/{location_id}/find-item-images", "post"),
        ("/api/v1/photos/vision-providers", "get"),
        ("/api/v1/system/cleanup-unused-crops", "post"),
    ]
    for path, method in expected:
        responses = paths[path][method]["responses"]
        ok_code = "200" if "200" in responses else "201"
        schema = responses[ok_code]["content"]["application/json"]["schema"]
        assert "$ref" in schema or schema.get("properties"), f"Expected typed response schema for {method.upper()} {path}, got {schema}"
