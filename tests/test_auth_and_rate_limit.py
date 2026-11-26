import io
from unittest.mock import patch

from PIL import Image


def _make_jpeg_bytes(color=(60, 120, 180), size=(120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_auth_disabled_by_default(client):
    res = client.get("/api/v1/auth/status")
    assert res.status_code == 200
    body = res.json()
    assert body["auth_enabled"] is False
    assert body["authenticated"] is True


def test_pin_auth_protects_api_and_uploads_with_cookie_and_bearer(client, monkeypatch):
    from app.core.config import settings

    # Seed an upload while auth is off so we can test /uploads/ access control once enabled
    up = client.post(
        "/api/v1/photos/upload",
        files={"file": ("seed.jpg", _make_jpeg_bytes(), "image/jpeg")},
    )
    assert up.status_code == 200
    upload_url = up.json()["original_path"]

    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", "4829")
    monkeypatch.setattr(settings, "WIMS_AUTH_BYPASS_LOCALHOST", False)

    # Public routes stay reachable
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/").status_code == 200
    status_unauth = client.get("/api/v1/auth/status")
    assert status_unauth.status_code == 200
    assert status_unauth.json()["auth_enabled"] is True
    assert status_unauth.json()["authenticated"] is False

    # Protected API & uploads return 401 with X-Request-ID and structured error envelope
    r_items = client.get("/api/v1/items", headers={"X-Request-ID": "auth-check-1"})
    assert r_items.status_code == 401
    assert r_items.headers.get("X-Request-ID") == "auth-check-1"
    assert r_items.json()["error"]["code"] == "unauthorized"

    assert client.post("/api/v1/locations", json={"name": "Blocked", "location_type": "room"}).status_code == 401
    assert client.get(upload_url).status_code == 401

    # Wrong PIN fails with 401
    bad_login = client.post("/api/v1/auth/login", json={"pin": "0000"})
    assert bad_login.status_code == 401
    assert bad_login.json()["error"]["code"] == "invalid_credentials"

    # Correct PIN sets HttpOnly wims_session cookie and grants access
    good_login = client.post("/api/v1/auth/login", json={"pin": "4829"})
    assert good_login.status_code == 200
    assert good_login.json()["authenticated"] is True
    set_cookie = good_login.headers.get("set-cookie", "").lower()
    assert "wims_session=" in set_cookie
    assert "httponly" in set_cookie

    assert client.get("/api/v1/items").status_code == 200
    assert client.get(upload_url).status_code == 200

    # Rotating PIN invalidates existing cookie immediately
    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", "9999")
    assert client.get("/api/v1/items").status_code == 401

    # Bearer token works without cookies
    client.cookies.clear()
    bearer_res = client.get("/api/v1/items", headers={"Authorization": "Bearer 9999"})
    assert bearer_res.status_code == 200

    # Logout clears cookie
    client.post("/api/v1/auth/login", json={"pin": "9999"})
    assert client.get("/api/v1/items").status_code == 200
    logout_res = client.post("/api/v1/auth/logout")
    assert logout_res.status_code == 200
    assert client.get("/api/v1/items").status_code == 401


def test_localhost_bypass_and_scoped_qr_phone_handoffs(client, monkeypatch):
    from app.core.config import settings
    from app.core.security import is_request_authenticated
    from starlette.datastructures import Headers

    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", "4829")
    monkeypatch.setattr(settings, "WIMS_AUTH_BYPASS_LOCALHOST", True)

    assert is_request_authenticated(Headers({}), "127.0.0.1") is True
    assert is_request_authenticated(Headers({}), "::1") is True
    assert is_request_authenticated(Headers({}), "192.168.1.44") is False

    # Disable localhost bypass to verify QR phone flows work when phone is on LAN
    monkeypatch.setattr(settings, "WIMS_AUTH_BYPASS_LOCALHOST", False)

    # Authenticated desktop creates capture session & unit-setup session
    headers = {"Authorization": "Bearer 4829"}
    cap = client.post("/api/v1/photos/capture-sessions", json={"label": "Bin A"}, headers=headers)
    assert cap.status_code == 201
    cap_token = cap.json()["token"]

    unit_sess = client.post("/api/v1/unit-setup/sessions", json={}, headers=headers)
    assert unit_sess.status_code == 201
    setup_token = unit_sess.json()["token"]

    # Phone (unauthenticated client) can upload to active capture session
    client.cookies.clear()
    phone_up = client.post(
        f"/api/v1/photos/capture-sessions/{cap_token}/upload",
        files={"file": ("phone.jpg", _make_jpeg_bytes(), "image/jpeg")},
    )
    assert phone_up.status_code == 200

    # Phone connecting to active unit-setup session gets scoped session cookie for drawer uploads
    conn = client.post(f"/api/v1/unit-setup/sessions/{setup_token}/connect")
    assert conn.status_code == 200
    assert "wims_session=" in conn.headers.get("set-cookie", "").lower()

    created_unit = client.post(
        f"/api/v1/unit-setup/sessions/{setup_token}/unit",
        json={"name": "LAN Shelf", "compartment_count": 2, "grid_cols": 1},
    )
    assert created_unit.status_code == 201
    drawer_id = created_unit.json()["drawers"][0]["id"]

    drawer_photo = client.post(
        f"/api/v1/locations/{drawer_id}/photos",
        files={"file": ("drawer.jpg", _make_jpeg_bytes(), "image/jpeg")},
    )
    assert drawer_photo.status_code == 201


def test_per_ip_sliding_window_rate_limits(client, monkeypatch):
    from app.core.config import settings
    from app.core.rate_limit import rate_limiter

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_PER_MIN", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_SYSTEM_PER_MIN", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_VISION_PER_MIN", 2)
    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", "4829")
    rate_limiter.reset()

    # Auth login rate limit
    assert client.post("/api/v1/auth/login", json={"pin": "1111"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"pin": "1111"}).status_code == 401
    blocked_auth = client.post("/api/v1/auth/login", json={"pin": "4829"})
    assert blocked_auth.status_code == 429
    assert blocked_auth.headers.get("Retry-After") is not None
    assert blocked_auth.json()["error"]["code"] == "rate_limited"

    # System endpoint rate limit
    headers = {"Authorization": "Bearer 4829"}
    assert client.post("/api/v1/system/cleanup-unused-crops", headers=headers).status_code == 200
    assert client.post("/api/v1/system/cleanup-unused-crops", headers=headers).status_code == 200
    blocked_sys = client.post("/api/v1/system/cleanup-unused-crops", headers=headers)
    assert blocked_sys.status_code == 429

    # Vision search rate limit
    with patch(
        "app.services.vision_service.vision_service.extract_search_query",
        return_value=("screw", False),
    ):
        for _ in range(2):
            r = client.post(
                "/api/v1/search/photo",
                files={"file": ("q.jpg", _make_jpeg_bytes(), "image/jpeg")},
                headers=headers,
            )
            assert r.status_code == 200
        blocked_vis = client.post(
            "/api/v1/search/photo",
            files={"file": ("q.jpg", _make_jpeg_bytes(), "image/jpeg")},
            headers=headers,
        )
        assert blocked_vis.status_code == 429
