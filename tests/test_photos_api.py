import io
from PIL import Image
from app.core.config import settings

def test_photo_upload_success_and_thumbnail_creation(client):
    # Create an in-memory test image
    img_byte_arr = io.BytesIO()
    img = Image.new("RGB", (800, 600), color=(73, 109, 137))
    img.save(img_byte_arr, format="JPEG")
    img_byte_arr.seek(0)

    files = {"file": ("test_container.jpg", img_byte_arr, "image/jpeg")}
    response = client.post("/api/v1/photos/upload", files=files)

    assert response.status_code == 200
    data = response.json()
    assert "original_path" in data
    assert "thumbnail_path" in data
    assert data["thumbnail_path"].endswith(".webp")

    # Verify physical file existence
    orig_name = data["filename"]
    thumb_name = orig_name.rsplit(".", 1)[0] + "_thumb.webp"
    assert (settings.ORIGINALS_DIR / orig_name).exists()
    assert (settings.THUMBNAILS_DIR / thumb_name).exists()

def test_photo_upload_invalid_extension(client):
    fake_file = io.BytesIO(b"not an executable")
    files = {"file": ("script.exe", fake_file, "application/octet-stream")}
    response = client.post("/api/v1/photos/upload", files=files)
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


def test_capture_session_lifecycle(client):
    create_resp = client.post("/api/v1/photos/capture-sessions", json={"label": "Item photo"})
    assert create_resp.status_code == 201
    session_data = create_resp.json()
    token = session_data["token"]
    assert token
    assert session_data["expires_in_seconds"] > 0
    assert f"/capture.html?token={token}" in session_data["capture_url"]
    assert session_data["qr_url"] == f"/api/v1/photos/capture-sessions/{token}/qr"

    status_resp = client.get(f"/api/v1/photos/capture-sessions/{token}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "pending"
    assert status_resp.json()["label"] == "Item photo"
    assert status_resp.json()["photo"] is None

    qr_resp = client.get(session_data["qr_url"])
    assert qr_resp.status_code == 200
    assert qr_resp.headers["content-type"] == "image/png"
    assert qr_resp.content.startswith(b"\x89PNG")

    img_byte_arr = io.BytesIO()
    Image.new("RGB", (640, 480), color=(40, 120, 180)).save(img_byte_arr, format="JPEG")
    img_byte_arr.seek(0)
    upload_resp = client.post(
        f"/api/v1/photos/capture-sessions/{token}/upload",
        files={"file": ("phone_snap.jpg", img_byte_arr, "image/jpeg")},
    )
    assert upload_resp.status_code == 200
    uploaded = upload_resp.json()
    assert uploaded["original_path"].startswith("/uploads/")
    assert (settings.ORIGINALS_DIR / uploaded["filename"]).exists()

    done_resp = client.get(f"/api/v1/photos/capture-sessions/{token}")
    assert done_resp.status_code == 200
    done_data = done_resp.json()
    assert done_data["status"] == "uploaded"
    assert done_data["photo"]["original_path"] == uploaded["original_path"]


def test_capture_session_not_found_and_expired(client):
    assert client.get("/api/v1/photos/capture-sessions/missing-token").status_code == 404
    assert client.get("/api/v1/photos/capture-sessions/missing-token/qr").status_code == 404

    img_byte_arr = io.BytesIO()
    Image.new("RGB", (100, 100), color=(10, 20, 30)).save(img_byte_arr, format="JPEG")
    img_byte_arr.seek(0)
    assert client.post(
        "/api/v1/photos/capture-sessions/missing-token/upload",
        files={"file": ("snap.jpg", img_byte_arr, "image/jpeg")},
    ).status_code == 404

    from datetime import datetime, timedelta, timezone
    from app.services.capture_session_service import capture_session_service

    created = client.post("/api/v1/photos/capture-sessions", json={}).json()
    token = created["token"]
    capture_session_service._sessions[token].expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)

    expired_status = client.get(f"/api/v1/photos/capture-sessions/{token}")
    assert expired_status.status_code == 200
    assert expired_status.json()["status"] == "expired"

    img_byte_arr.seek(0)
    expired_upload = client.post(
        f"/api/v1/photos/capture-sessions/{token}/upload",
        files={"file": ("snap.jpg", img_byte_arr, "image/jpeg")},
    )
    assert expired_upload.status_code == 410


