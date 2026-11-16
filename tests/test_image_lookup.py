from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image


def _make_png_bytes(color=(30, 144, 255), size=(120, 120)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_find_and_import_item_image_updates_item_photo(client):
    loc = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    item = client.post(
        "/api/v1/items",
        json={
            "name": "L7824",
            "serial_or_part_number": "L7824",
            "category": "Voltage Regulator",
            "location_id": loc["id"],
            "quantity": 5,
        },
    ).json()

    png_bytes = _make_png_bytes()

    commons_json = {
        "query": {
            "pages": {
                "123": {
                    "title": "File:L7824_TO-220_voltage_regulator.jpg",
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/L7824.jpg",
                            "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/L7824.jpg/640px-L7824.jpg",
                            "mime": "image/jpeg",
                            "width": 800,
                            "height": 600,
                        }
                    ],
                }
            }
        }
    }

    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = commons_json

    mock_img_resp = MagicMock()
    mock_img_resp.status_code = 200
    mock_img_resp.headers = {"content-type": "image/png"}
    mock_img_resp.content = png_bytes

    async def fake_get(url, *args, **kwargs):
        if "api.php" in str(url):
            return mock_search_resp
        return mock_img_resp

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.image_lookup_service.httpx.AsyncClient", return_value=mock_cm):
        resp = client.post(f"/api/v1/items/{item['id']}/find-image")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["item_photo"].startswith("/uploads/originals/")
    assert data["item"]["id"] == item["id"]
    assert data["item"]["item_photo"] == data["item_photo"]

    fetched = client.get(f"/api/v1/items/{item['id']}").json()
    assert fetched["item_photo"] == data["item_photo"]


def test_find_item_image_returns_404_when_none_found(client):
    loc = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    item = client.post(
        "/api/v1/items",
        json={"name": "Unknown Mystery Part XYZ999", "location_id": loc["id"], "quantity": 1},
    ).json()

    mock_empty_resp = MagicMock()
    mock_empty_resp.status_code = 200
    mock_empty_resp.json.return_value = {"query": {}}

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_empty_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.image_lookup_service.httpx.AsyncClient", return_value=mock_cm):
        resp = client.post(f"/api/v1/items/{item['id']}/find-image")

    assert resp.status_code == 404


def test_bulk_location_find_images_replaces_crops_and_preserves_custom_photos(client):
    loc = client.post("/api/v1/locations", json={"name": "Voltage Regulators", "location_type": "box"}).json()
    it_crop = client.post(
        "/api/v1/items",
        json={
            "name": "L7818",
            "location_id": loc["id"],
            "quantity": 5,
            "item_photo": "/uploads/originals/old_crop_1.jpg",
        },
    ).json()
    it_custom = client.post(
        "/api/v1/items",
        json={
            "name": "L7805",
            "location_id": loc["id"],
            "quantity": 5,
            "item_photo": "/uploads/originals/user_custom.jpg",
        },
    ).json()

    png_bytes = _make_png_bytes((20, 180, 90))
    commons_json = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:78xx_regulator.jpg",
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/wikipedia/commons/7/78/78xx.jpg",
                            "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/78/78xx.jpg/640px-78xx.jpg",
                            "mime": "image/jpeg",
                            "width": 640,
                            "height": 480,
                        }
                    ],
                }
            }
        }
    }
    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = commons_json
    mock_img_resp = MagicMock()
    mock_img_resp.status_code = 200
    mock_img_resp.headers = {"content-type": "image/png"}
    mock_img_resp.content = png_bytes

    async def fake_get(url, *args, **kwargs):
        if "api.php" in str(url):
            return mock_search_resp
        return mock_img_resp

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.image_lookup_service.httpx.AsyncClient", return_value=mock_cm):
        resp = client.post(f"/api/v1/locations/{loc['id']}/find-item-images")

    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["updated_count"] == 1

    updated_crop = client.get(f"/api/v1/items/{it_crop['id']}").json()
    unchanged_custom = client.get(f"/api/v1/items/{it_custom['id']}").json()
    assert updated_crop["item_photo"].startswith("/uploads/originals/")
    assert updated_crop["item_photo"] != "/uploads/originals/old_crop_1.jpg"
    assert unchanged_custom["item_photo"] == "/uploads/originals/user_custom.jpg"
