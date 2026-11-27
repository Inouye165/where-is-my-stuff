from ipaddress import ip_address
import io
from pathlib import Path
import shutil
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography import x509
from PIL import Image

from app.core.config import settings
from app.core.network import resolve_mobile_camera_base_url
from app.core.tls import ensure_lan_tls_cert
from app.services.vision_service import vision_service


def _make_jpeg_bytes(color=(70, 120, 180), size=(240, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_existing_unit_setup_session_enriches_coords_labels_and_high_drawer_index(client):
    room = client.post("/api/v1/locations", json={"name": "Workshop", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Yellow Organizer", "location_type": "container", "parent_id": room["id"]},
    ).json()
    client.put(
        f"/api/v1/locations/{cabinet['id']}/grid",
        json={
            "grid_rows": 2,
            "grid_cols": 2,
            "slots": [
                {"name": "12V Gear Motor", "location_type": "drawer", "grid_row": 1, "grid_col": 1, "grid_col_span": 2},
                {"name": "ESP32-C3", "location_type": "drawer", "grid_row": 2, "grid_col": 1},
            ],
        },
    )
    # Give first drawer a label & description
    slots = client.get(f"/api/v1/locations/{cabinet['id']}").json()
    assert slots["id"] == cabinet["id"]
    tree = client.get("/api/v1/locations/tree").json()
    ws_node = next(n for n in tree if n["id"] == room["id"])
    cab_node = next(c for c in ws_node["children"] if c["id"] == cabinet["id"])
    first_drawer = min(cab_node["children"], key=lambda d: (d["grid_row"], d["grid_col"]))
    client.patch(
        f"/api/v1/locations/{first_drawer['id']}",
        json={"label": "MTR-1", "description": "Brushless & brushed DC motors"},
    )

    # Missing unit_id -> 404
    missing = client.post("/api/v1/unit-setup/sessions", json={"unit_id": 999999})
    assert missing.status_code == 404

    # Existing unit -> starts directly in capturing_drawers with room resolved
    sess_resp = client.post("/api/v1/unit-setup/sessions", json={"unit_id": cabinet["id"]})
    assert sess_resp.status_code == 201
    sess = sess_resp.json()
    assert sess["status"] == "capturing_drawers"
    assert sess["unit_id"] == cabinet["id"]
    assert sess["unit_name"] == "Yellow Organizer"
    assert sess["room_id"] == room["id"]
    assert sess["room_name"] == "Workshop"

    token = sess["token"]
    conn = client.post(f"/api/v1/unit-setup/sessions/{token}/connect")
    assert conn.status_code == 200
    cdata = conn.json()
    assert cdata["status"] == "capturing_drawers"
    assert len(cdata["drawers"]) == 2
    d0 = cdata["drawers"][0]
    assert d0["coord"] == "A1\u2013B1"
    assert d0["label"] == "MTR-1"
    assert d0["description"] == "Brushless & brushed DC motors"
    assert d0["grid_col_span"] == 2

    # Supports organizers with >50 drawers
    prog = client.patch(
        f"/api/v1/unit-setup/sessions/{token}/progress",
        json={"current_drawer_index": 100, "status": "capturing_drawers"},
    )
    assert prog.status_code == 200
    assert prog.json()["current_drawer_index"] == 100


def test_multi_angle_analyze_photo_sends_all_images_and_extracts_part_number_and_notes(client):
    drawer = client.post(
        "/api/v1/locations",
        json={
            "name": "DC Motors",
            "location_type": "drawer",
            "description": "Small hobby motors and encoders",
        },
    ).json()

    photo_ids = []
    for idx, color in enumerate(((80, 90, 100), (120, 130, 140), (160, 170, 180))):
        up = client.post(
            f"/api/v1/locations/{drawer['id']}/photos",
            files={"file": (f"angle_{idx}.jpg", _make_jpeg_bytes(color=color), "image/jpeg")},
        )
        assert up.status_code == 201
        photo_ids.append(up.json()["id"])

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "content": (
                '{"items": [{"name": "NEMA 17 Stepper Motor", "part_number": "17HS4401", '
                '"category": "Motors", "estimated_qty": 2, '
                '"brief_specs": "12V 1.7A 40Ncm 4-wire bipolar; D-shaft 5mm", '
                '"bbox": [100, 100, 900, 900]}]}'
            )
        }
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)) as mock_post:
        resp = client.post(
            f"/api/v1/locations/{drawer['id']}/analyze-photo",
            data={"photo_ids": ",".join(str(i) for i in photo_ids), "provider": "local"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["name"] == "NEMA 17 Stepper Motor"
    assert item["part_number"] == "17HS4401"
    assert "40Ncm" in item["brief_specs"]
    assert item["crop_photo_path"] and item["crop_photo_path"].startswith("/uploads/originals/")

    # Single Ollama request must include all 3 images + multi-angle & drawer label context
    assert mock_post.await_count == 1
    sent_json = mock_post.await_args.kwargs["json"]
    msg = sent_json["messages"][0]
    assert len(msg["images"]) == 3
    assert "MULTIPLE photos" in msg["content"]
    assert "DC Motors" in msg["content"]

    # Verify Gemini and OpenAI multi-image payload builders
    gem_payload = vision_service._build_gemini_payload("test", [("b1", "image/jpeg"), ("b2", "image/png")])
    assert len(gem_payload["contents"][0]["parts"]) == 3
    oai_payload = vision_service._build_openai_payload("test", "gpt-4o-mini", [("b1", "image/jpeg"), ("b2", "image/png")])
    assert len(oai_payload["messages"][0]["content"]) == 3


def test_multi_angle_analyze_photo_idor_and_validation_guards(client):
    d1 = client.post("/api/v1/locations", json={"name": "Drawer A", "location_type": "drawer"}).json()
    d2 = client.post("/api/v1/locations", json={"name": "Drawer B", "location_type": "drawer"}).json()

    p2 = client.post(
        f"/api/v1/locations/{d2['id']}/photos",
        files={"file": ("b.jpg", _make_jpeg_bytes(), "image/jpeg")},
    ).json()

    # Cross-location IDOR blocked
    idor = client.post(
        f"/api/v1/locations/{d1['id']}/analyze-photo",
        data={"photo_ids": str(p2["id"])},
    )
    assert idor.status_code == 404

    # Non-numeric photo_ids rejected
    bad_fmt = client.post(
        f"/api/v1/locations/{d1['id']}/analyze-photo",
        data={"photo_ids": "1,abc"},
    )
    assert bad_fmt.status_code == 400

    # >8 photos rejected
    too_many = client.post(
        f"/api/v1/locations/{d1['id']}/analyze-photo",
        data={"photo_ids": "1,2,3,4,5,6,7,8,9"},
    )
    assert too_many.status_code == 400


def test_existing_unit_capture_pin_auth_and_frontend_syntax(client, monkeypatch):
    cabinet = client.post("/api/v1/locations", json={"name": "Auth Cabinet", "location_type": "container"}).json()

    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", "1234")
    monkeypatch.setattr(settings, "WIMS_AUTH_BYPASS_LOCALHOST", False)

    unauth_create = client.post("/api/v1/unit-setup/sessions", json={"unit_id": cabinet["id"]})
    assert unauth_create.status_code == 401

    auth_create = client.post(
        "/api/v1/unit-setup/sessions",
        json={"unit_id": cabinet["id"]},
        headers={"Authorization": "Bearer 1234"},
    )
    assert auth_create.status_code == 201
    token = auth_create.json()["token"]

    # QR-scoped endpoints work without PIN
    assert client.post(f"/api/v1/unit-setup/sessions/{token}/connect").status_code == 200
    assert (
        client.patch(
            f"/api/v1/unit-setup/sessions/{token}/progress",
            json={"current_drawer_index": 0, "status": "capturing_drawers"},
        ).status_code
        == 200
    )

    # Frontend JS syntax + entry points check
    js_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    mobile_js = (js_dir / "mobile-unit-setup.js").read_text(encoding="utf-8")
    assert "openExistingUnitCaptureWizard(" in (js_dir / "views.js").read_text(encoding="utf-8")
    assert "usw-scan-autoinsert-btn" in (js_dir / "unit-setup-wizard.js").read_text(encoding="utf-8")
    assert "setup-coord-badge" in mobile_js
    assert "live-cam-retake-btn" in mobile_js
    assert "setup-jump-toggle-btn" in mobile_js
    assert "Open Camera" in mobile_js

    node_exe = shutil.which("node")
    if node_exe:
        for js_name in ("mobile-unit-setup.js", "label-capture-page.js", "unit-setup-wizard.js", "views.js", "app.js"):
            proc = subprocess.run([node_exe, "--check", str(js_dir / js_name)], capture_output=True, text=True)
            assert proc.returncode == 0, f"Syntax error in {js_name}:\n{proc.stderr}"


def test_active_session_discovery_and_phone_shortcut(client, monkeypatch):
    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", None)
    monkeypatch.setattr("app.core.network.get_lan_ip", lambda: "192.168.1.42")
    monkeypatch.setenv("WIMS_HTTPS_ACTIVE", "1")
    monkeypatch.setenv("WIMS_HTTPS_PORT", "8444")

    # No active session yet
    assert client.get("/api/v1/unit-setup/active").status_code == 404
    no_sess_redir = client.get("/phone", follow_redirects=False)
    assert no_sess_redir.status_code == 302
    assert no_sess_redir.headers["location"] == "/setup.html"

    # Create two sessions; /active and /phone must resolve to the newest active one
    s1 = client.post("/api/v1/unit-setup/sessions", json={}).json()
    s2 = client.post("/api/v1/unit-setup/sessions", json={}).json()
    assert s2["token"] != s1["token"]

    active_resp = client.get("/api/v1/unit-setup/active", headers={"Host": "localhost:8000"})
    assert active_resp.status_code == 200
    adata = active_resp.json()
    assert adata["token"] == s2["token"]
    assert adata["mobile_url"] == f"https://192.168.1.42:8444/setup.html?token={s2['token']}"
    assert adata["http_url"] == f"http://192.168.1.42:{settings.PORT}/setup.html?token={s2['token']}"

    phone_redir = client.get("/phone", headers={"Host": "localhost:8000"}, follow_redirects=False)
    assert phone_redir.status_code == 302
    assert phone_redir.headers["location"] == f"https://192.168.1.42:8444/setup.html?token={s2['token']}"

    # When PIN auth is enabled, /active requires auth and /phone does not leak tokens
    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", "1234")
    monkeypatch.setattr(settings, "WIMS_AUTH_BYPASS_LOCALHOST", False)
    assert client.get("/api/v1/unit-setup/active").status_code == 401
    assert client.get("/api/v1/unit-setup/active", headers={"Authorization": "Bearer 1234"}).status_code == 200
    pin_phone = client.get("/phone", follow_redirects=False)
    assert pin_phone.status_code == 302
    assert pin_phone.headers["location"] == "/setup.html"


def test_lan_tls_cert_creation_reuse_and_ip_change(tmp_path):
    cert1, key1 = ensure_lan_tls_cert(tmp_path, "192.168.1.50")
    assert cert1.exists() and key1.exists()

    parsed1 = x509.load_pem_x509_certificate(cert1.read_bytes())
    san1 = parsed1.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san1.get_values_for_type(x509.DNSName)
    ip_sans1 = set(san1.get_values_for_type(x509.IPAddress))
    assert ip_address("127.0.0.1") in ip_sans1
    assert ip_address("192.168.1.50") in ip_sans1

    # Repeat call with the same LAN IP must reuse without regenerating
    cert_bytes_before = cert1.read_bytes()
    key_bytes_before = key1.read_bytes()
    cert2, key2 = ensure_lan_tls_cert(tmp_path, "192.168.1.50")
    assert cert2 == cert1 and key2 == key1
    assert cert2.read_bytes() == cert_bytes_before
    assert key2.read_bytes() == key_bytes_before

    # Changing LAN IP must regenerate certificate with updated IP SAN
    cert3, _ = ensure_lan_tls_cert(tmp_path, "10.0.0.88")
    parsed3 = x509.load_pem_x509_certificate(cert3.read_bytes())
    san3 = parsed3.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ip_sans3 = set(san3.get_values_for_type(x509.IPAddress))
    assert ip_address("10.0.0.88") in ip_sans3
    assert ip_address("127.0.0.1") in ip_sans3


def test_mobile_camera_url_uses_https_when_active_and_setup_csp_allows_camera_stream(client, monkeypatch):
    # Unset -> HTTP fallback
    monkeypatch.delenv("WIMS_HTTPS_ACTIVE", raising=False)
    monkeypatch.delenv("WIMS_HTTPS_PORT", raising=False)
    monkeypatch.setattr("app.core.network.get_lan_ip", lambda: "192.168.1.42")
    monkeypatch.setattr(settings, "HTTPS_PORT", 8443)
    http_url = resolve_mobile_camera_base_url(host_header="localhost:8000")
    assert http_url == f"http://192.168.1.42:{settings.PORT}"

    # Active -> HTTPS with configured default port 8443, including via session creation API
    monkeypatch.setenv("WIMS_HTTPS_ACTIVE", "1")
    https_url = resolve_mobile_camera_base_url(host_header="localhost:8000")
    assert https_url == "https://192.168.1.42:8443"

    # Active with fallback WIMS_HTTPS_PORT override (e.g. 8444 when 8443 is busy)
    monkeypatch.setenv("WIMS_HTTPS_PORT", "8444")
    https_override_url = resolve_mobile_camera_base_url(host_header="localhost:8000")
    assert https_override_url == "https://192.168.1.42:8444"

    sess_resp = client.post("/api/v1/unit-setup/sessions", headers={"Host": "localhost:8000"})
    assert sess_resp.status_code == 201
    mobile_url = sess_resp.json()["mobile_url"]
    assert mobile_url.startswith("https://192.168.1.42:8444/setup.html?token=")

    # /setup.html CSP permits live camera streams
    setup_page = client.get("/setup.html")
    assert setup_page.status_code == 200
    csp = setup_page.headers.get("Content-Security-Policy", "")
    assert "media-src 'self' blob: mediastream:" in csp
