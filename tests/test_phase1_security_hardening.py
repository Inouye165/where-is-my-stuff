import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image
import pytest

from app.core.config import settings
from app.core.network import resolve_effective_base_url, resolve_mobile_camera_base_url
from app.core.rate_limit import rate_limiter
from app.services.image_lookup_service import download_and_save_image, verify_response_peer_is_public
from app.services.item_bundle_service import _MAX_URL_BYTES, item_bundle_service


def _make_jpeg_bytes(color=(40, 110, 180), size=(120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_phone_shortcut_rejects_spoofed_host_header(client, monkeypatch):
    monkeypatch.setattr(settings, "WIMS_ACCESS_PIN", None)
    monkeypatch.setattr("app.core.network.get_lan_ip", lambda: "192.168.1.42")
    monkeypatch.setenv("WIMS_HTTPS_ACTIVE", "1")
    monkeypatch.setenv("WIMS_HTTPS_PORT", "8443")

    sess = client.post("/api/v1/unit-setup/sessions", json={}).json()
    redir = client.get("/phone", headers={"Host": "evil.com"}, follow_redirects=False)
    assert redir.status_code == 302
    loc = redir.headers["location"]
    assert "evil.com" not in loc
    assert loc == f"https://192.168.1.42:8443/setup.html?token={sess['token']}"


def test_base_url_resolution_blocks_spoofed_and_malformed_hosts_while_allowing_lan(monkeypatch):
    monkeypatch.setattr("app.core.network.get_lan_ip", lambda: "192.168.1.42")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", None)

    for bad_host in ("evil.com", "evil.com:8080", "evil.com/phish", "user@evil.com", "bad host"):
        assert resolve_effective_base_url(host_header=bad_host) == f"http://192.168.1.42:{settings.PORT}"

    monkeypatch.setenv("WIMS_HTTPS_ACTIVE", "1")
    monkeypatch.setenv("WIMS_HTTPS_PORT", "8443")
    assert resolve_mobile_camera_base_url(host_header="evil.com") == "https://192.168.1.42:8443"

    assert resolve_effective_base_url(host_header="workshop.local:8000") == "http://workshop.local:8000"
    assert resolve_effective_base_url(host_header="workshop-pi:8000") == "http://workshop-pi:8000"
    assert resolve_effective_base_url(host_header="192.168.1.50:8000") == "http://192.168.1.50:8000"
    assert resolve_effective_base_url(host_header="100.64.0.5:8000") == "http://100.64.0.5:8000"
    assert resolve_effective_base_url(host_header="mybox.ts.net:8000") == "http://mybox.ts.net:8000"

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://inventory.example.com")
    assert resolve_effective_base_url(host_header="evil.com") == "https://inventory.example.com"


def test_qr_endpoint_rejects_external_phishing_targets_and_allows_local_or_configured(client, monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", None)
    loc = client.post("/api/v1/locations", json={"name": "QR Drawer", "location_type": "drawer"}).json()

    r1 = client.get(f"/api/v1/locations/{loc['id']}/qr", params={"target_url": "https://evil.example/phish"})
    assert r1.status_code == 400
    r2 = client.get(f"/api/v1/locations/{loc['id']}/qr", params={"base_url": "https://evil.example"})
    assert r2.status_code == 400

    ok_lan = client.get(f"/api/v1/locations/{loc['id']}/qr", params={"base_url": "http://192.168.1.50:8000"})
    assert ok_lan.status_code == 200

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://inventory.example.com")
    ok_pub = client.get(
        f"/api/v1/locations/{loc['id']}/qr",
        params={"target_url": f"https://inventory.example.com/#/drawer/{loc['id']}"},
    )
    assert ok_pub.status_code == 200


@pytest.mark.asyncio
async def test_bundle_url_fetch_streams_with_byte_cap_and_blocks_non_public_peer(monkeypatch):
    monkeypatch.setattr("app.services.item_bundle_service._is_safe_public_url", lambda _u: True)

    class FakeStreamResp:
        def __init__(self, peer_ip: str, chunks: list[bytes], status_code: int = 200):
            self.status_code = status_code
            self.headers = {"content-type": "text/html; charset=utf-8"}
            self.encoding = "utf-8"
            self._chunks = chunks
            self.extensions = {
                "network_stream": SimpleNamespace(get_extra_info=lambda k: (peer_ip, 443) if k == "peername" else None)
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def aiter_bytes(self):
            for c in self._chunks:
                yield c

    # 1. Private peer IP (DNS rebinding) must be rejected before reading body
    private_resp = FakeStreamResp("127.0.0.1", [b"<html><title>Secret</title></html>"])
    client_private = MagicMock()
    client_private.stream = MagicMock(return_value=private_resp)
    cm_private = MagicMock()
    cm_private.__aenter__ = AsyncMock(return_value=client_private)
    cm_private.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.item_bundle_service.httpx.AsyncClient", return_value=cm_private):
        text_priv, warn_priv = await item_bundle_service._fetch_public_url_text("https://rebind.example/page")
    assert text_priv is None
    assert warn_priv and "Blocked non-public peer" in warn_priv

    # 2. Public peer IP with >1MB streamed body must be truncated cleanly to _MAX_URL_BYTES
    oversized_chunks = [b"<title>Sensor</title>" + b"A" * (_MAX_URL_BYTES // 2), b"B" * _MAX_URL_BYTES]
    public_resp = FakeStreamResp("93.184.216.34", oversized_chunks)
    client_public = MagicMock()
    client_public.stream = MagicMock(return_value=public_resp)
    cm_public = MagicMock()
    cm_public.__aenter__ = AsyncMock(return_value=client_public)
    cm_public.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.item_bundle_service.httpx.AsyncClient", return_value=cm_public):
        text_pub, warn_pub = await item_bundle_service._fetch_public_url_text("https://example.com/product")
    assert warn_pub is None
    assert text_pub is not None
    assert "Title: Sensor" in text_pub
    assert "B" not in text_pub


def test_analyze_bundle_caps_files_and_photo_paths(client):
    too_many_files = [
        ("files", (f"f{i}.png", _make_jpeg_bytes(), "image/jpeg"))
        for i in range(21)
    ]
    r_files = client.post("/api/v1/items/analyze-bundle", files=too_many_files)
    assert r_files.status_code == 400

    too_many_paths = {"photo_paths": [f"/uploads/originals/img_{i}.jpg" for i in range(21)]}
    r_paths = client.post("/api/v1/items/analyze-bundle", data=too_many_paths)
    assert r_paths.status_code == 400


@pytest.mark.asyncio
async def test_image_lookup_aborts_on_private_peer_ip(monkeypatch):
    monkeypatch.setattr("app.services.image_lookup_service._host_resolves_publicly", lambda _h: True)

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "image/jpeg"}
    fake_resp.extensions = {
        "network_stream": SimpleNamespace(get_extra_info=lambda k: ("169.254.169.254", 443) if k == "peername" else None)
    }
    assert verify_response_peer_is_public(fake_resp) is False

    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=fake_resp)
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    fake_client = MagicMock()
    fake_client.stream = MagicMock(return_value=stream_cm)

    saved = await download_and_save_image(fake_client, "https://rebind.example/meta.jpg")
    assert saved is None


def test_rate_limit_enforced_on_analyze_bundle_and_analyze_pdf(client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_VISION_PER_MIN", 1)
    rate_limiter.reset()

    loc = client.post("/api/v1/locations", json={"name": "Rate Bin", "location_type": "bin"}).json()

    r1 = client.post("/api/v1/items/analyze-bundle", data={"location_id": str(loc["id"])})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/items/analyze-bundle", data={"location_id": str(loc["id"])})
    assert r2.status_code == 429

    from app.schemas.item import VisionAnalysisResponse

    rate_limiter.reset()
    pdf_bytes = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    with patch(
        "app.services.vision_service.vision_service.analyze_pdf",
        new=AsyncMock(return_value=VisionAnalysisResponse(status="fallback", items=[], message="ok")),
    ):
        p1 = client.post(
            f"/api/v1/locations/{loc['id']}/analyze-pdf",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
        )
        assert p1.status_code == 200
        p2 = client.post(
            f"/api/v1/locations/{loc['id']}/analyze-pdf",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
        )
        assert p2.status_code == 429


def test_schema_bounds_reject_javascript_links_and_oversized_lists(client):
    loc = client.post("/api/v1/locations", json={"name": "Bounds Cabinet", "location_type": "container"}).json()

    bad_link = client.post(
        "/api/v1/items/from-bundle",
        json={
            "name": "Bad Link Item",
            "location_id": loc["id"],
            "reference_links": ["javascript:alert(1)"],
        },
    )
    assert bad_link.status_code == 422

    too_many_bulk = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": loc["id"],
            "items": [{"name": f"Part {i}", "quantity": 1} for i in range(501)],
        },
    )
    assert too_many_bulk.status_code == 422

    too_many_slots = client.put(
        f"/api/v1/locations/{loc['id']}/grid",
        json={
            "grid_rows": 26,
            "grid_cols": 26,
            "slots": [
                {"name": f"S{i}", "location_type": "drawer", "grid_row": 1, "grid_col": 1}
                for i in range(677)
            ],
        },
    )
    assert too_many_slots.status_code == 422


@pytest.mark.asyncio
async def test_label_scan_analyze_uses_short_lived_session_scope_not_request_session(client, db_session):
    from app.schemas.label_scan import LabelScanAnalyzeRequest, QuadPoint
    from app.services.label_scan_service import label_scan_service

    cab = client.post(
        "/api/v1/locations",
        json={"name": "Async Scope Cabinet", "location_type": "container", "grid_rows": 1, "grid_cols": 1},
    ).json()
    sess = client.post("/api/v1/label-scan/sessions", json={"container_id": cab["id"]}).json()
    token = sess["token"]

    img_bytes = _make_jpeg_bytes(size=(400, 300))
    up = client.post(
        f"/api/v1/label-scan/sessions/{token}/upload",
        files={"file": ("cab.jpg", img_bytes, "image/jpeg")},
    ).json()

    # Pass a proxy session whose execute/get/commit raise if touched directly on the event loop
    proxy_db = MagicMock()
    proxy_db.get_bind.return_value = db_session.get_bind()
    proxy_db.execute.side_effect = AssertionError("Request session must not be queried directly in async analyze")
    proxy_db.get.side_effect = AssertionError("Request session must not be used directly in async analyze")
    proxy_db.flush.side_effect = AssertionError("Request session must not be flushed directly in async analyze")

    req = LabelScanAnalyzeRequest(
        photo_path=up["original_path"],
        quad=[QuadPoint(x=0.0, y=0.0), QuadPoint(x=1.0, y=0.0), QuadPoint(x=1.0, y=1.0), QuadPoint(x=0.0, y=1.0)],
        use_ollama_refinement=False,
    )
    with patch("app.services.label_scan_service._get_rapidocr", return_value=object()), patch(
        "app.services.label_scan_service._run_rapidocr_on_crop", return_value=("M3 Screws", 0.95)
    ), patch("app.services.label_scan_service._call_vision_label_batch", new=AsyncMock(return_value=({}, "none"))):
        res = await label_scan_service.analyze_session_photo(proxy_db, token, req)

    assert res.status == "success"
    assert res.summary.total == 1
