from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image


def _make_jpeg_bytes(color=(220, 180, 40), size=(120, 90)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_location_multiple_photos_lifecycle(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Yellow storage organizer", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "A1 Stepper Motors", "location_type": "drawer", "parent_id": cabinet["id"]},
    ).json()

    # Upload 2 photos to the drawer
    p1 = client.post(
        f"/api/v1/locations/{drawer['id']}/photos",
        files={"file": ("shot1.jpg", _make_jpeg_bytes((200, 50, 50)), "image/jpeg")},
    )
    assert p1.status_code == 201, p1.text
    p1_data = p1.json()
    assert p1_data["location_id"] == drawer["id"]
    assert p1_data["photo_path"].startswith("/uploads/originals/")

    p2 = client.post(
        f"/api/v1/locations/{drawer['id']}/photos",
        files={"file": ("shot2.jpg", _make_jpeg_bytes((50, 200, 50)), "image/jpeg")},
    )
    assert p2.status_code == 201, p2.text
    p2_data = p2.json()
    assert p2_data["id"] != p1_data["id"]

    # List photos for drawer
    list_res = client.get(f"/api/v1/locations/{drawer['id']}/photos")
    assert list_res.status_code == 200
    photos = list_res.json()
    assert len(photos) == 2

    # LocationRead includes photos array and sets container_photo
    loc_res = client.get(f"/api/v1/locations/{drawer['id']}")
    assert loc_res.status_code == 200
    loc_data = loc_res.json()
    assert len(loc_data["photos"]) == 2
    assert loc_data["container_photo"] is not None

    # Delete first photo -> second remains and container_photo updates cleanly
    del_res = client.delete(f"/api/v1/locations/{drawer['id']}/photos/{p1_data['id']}")
    assert del_res.status_code == 204

    list_after = client.get(f"/api/v1/locations/{drawer['id']}/photos").json()
    assert len(list_after) == 1
    assert list_after[0]["id"] == p2_data["id"]


def test_multi_photo_capture_session_attaches_to_location(client):
    drawer = client.post(
        "/api/v1/locations", json={"name": "B2 Buck Converters", "location_type": "drawer"}
    ).json()

    sess_res = client.post(
        "/api/v1/photos/capture-sessions",
        json={"label": "B2 Buck Converters", "location_id": drawer["id"]},
    )
    assert sess_res.status_code == 201, sess_res.text
    token = sess_res.json()["token"]

    # Upload 2 photos via the phone capture session
    up1 = client.post(
        f"/api/v1/photos/capture-sessions/{token}/upload",
        files={"file": ("phone1.jpg", _make_jpeg_bytes((10, 100, 200)), "image/jpeg")},
    )
    assert up1.status_code == 200, up1.text

    up2 = client.post(
        f"/api/v1/photos/capture-sessions/{token}/upload",
        files={"file": ("phone2.jpg", _make_jpeg_bytes((100, 10, 200)), "image/jpeg")},
    )
    assert up2.status_code == 200, up2.text

    # Session status reports both photos
    status_res = client.get(f"/api/v1/photos/capture-sessions/{token}")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["status"] == "uploaded"
    assert len(status_data["photos"]) == 2
    assert status_data["location_id"] == drawer["id"]

    # Both photos are saved on the drawer
    drawer_photos = client.get(f"/api/v1/locations/{drawer['id']}/photos").json()
    assert len(drawer_photos) == 2


def test_vision_analysis_local_ollama_and_paid_gemini(client):
    drawer = client.post(
        "/api/v1/locations", json={"name": "C1 ESP32", "location_type": "drawer"}
    ).json()
    photo = client.post(
        f"/api/v1/locations/{drawer['id']}/photos",
        files={"file": ("esp32.jpg", _make_jpeg_bytes((30, 30, 30)), "image/jpeg")},
    ).json()

    # Mock Ollama /api/chat response for local provider
    mock_ollama_resp = MagicMock()
    mock_ollama_resp.status_code = 200
    mock_ollama_resp.json.return_value = {
        "message": {
            "content": '{"items": [{"name": "ESP32 DevKit", "category": "Electronics", "estimated_qty": 4, "brief_specs": "USB-C"}]}'
        }
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_ollama_resp)):
        res = client.post(
            f"/api/v1/locations/{drawer['id']}/analyze-photo",
            data={"photo_id": str(photo["id"]), "provider": "local", "model": "llama3.2-vision"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "success"
    assert body["provider_used"] == "local"
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "ESP32 DevKit"
    assert body["items"][0]["estimated_qty"] == 4

    # Mock Gemini response for paid provider
    mock_gemini_resp = MagicMock()
    mock_gemini_resp.status_code = 200
    mock_gemini_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": '{"items": [{"name": "OLED Display", "category": "Electronics", "estimated_qty": 2, "brief_specs": "0.96 inch"}]}'
                        }
                    ]
                }
            }
        ]
    }
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "httpx.AsyncClient.post", new=AsyncMock(return_value=mock_gemini_resp)
    ):
        res_paid = client.post(
            f"/api/v1/locations/{drawer['id']}/analyze-photo",
            data={"photo_id": str(photo["id"]), "provider": "gemini"},
        )
    assert res_paid.status_code == 200, res_paid.text
    paid_body = res_paid.json()
    assert paid_body["status"] == "success"
    assert paid_body["provider_used"] == "gemini"
    assert paid_body["items"][0]["name"] == "OLED Display"


def test_vision_providers_endpoint_and_error_fallbacks(client):
    with patch("httpx.AsyncClient.get", side_effect=Exception("offline")):
        res = client.get("/api/v1/photos/vision-providers")
    assert res.status_code == 200
    data = res.json()
    assert "providers" in data
    assert "local" in data["providers"]
    assert "gemini" in data["providers"]
    assert "openai" in data["providers"]
    assert data["providers"]["local"]["available"] is False

    drawer = client.post("/api/v1/locations", json={"name": "D1", "location_type": "drawer"}).json()
    photo = client.post(
        f"/api/v1/locations/{drawer['id']}/photos",
        files={"file": ("d1.jpg", _make_jpeg_bytes((60, 60, 60)), "image/jpeg")},
    ).json()

    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.json.return_value = {"error": "model error"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=err_resp)):
        fb = client.post(
            f"/api/v1/locations/{drawer['id']}/analyze-photo",
            data={"photo_id": str(photo["id"]), "provider": "local"},
        )
    assert fb.status_code == 200
    fb_data = fb.json()
    assert fb_data["status"] == "fallback"
    assert "500" in fb_data["message"]


def test_drawer_and_item_view_regression_invariants():
    from pathlib import Path

    views_src = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "views.js").read_text(
        encoding="utf-8"
    )
    assert "drawer-items-list" in views_src
    assert "drawer-compact-carousel" in views_src
    assert "hier.unit.container_photo" in views_src
    assert "No container photo" in views_src
    assert "item-attachments-list-" in views_src
