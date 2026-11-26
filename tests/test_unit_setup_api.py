from datetime import datetime, timedelta, timezone
import io
from PIL import Image

from app.schemas.item import DetectedItem, VisionAnalysisResponse
from app.services.vision_service import vision_service


def _make_jpeg_bytes(color=(60, 120, 180)) -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), color=color).save(buf, format="JPEG")
    buf.seek(0)
    return buf


def test_create_unit_setup_session_room_and_unassigned_and_missing(client):
    room_resp = client.post("/api/v1/locations", json={"name": "Bedroom", "location_type": "room"})
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    # With valid room_id
    sess_resp = client.post("/api/v1/unit-setup/sessions", json={"room_id": room_id})
    assert sess_resp.status_code == 201
    data = sess_resp.json()
    assert data["token"]
    assert data["room_id"] == room_id
    assert data["room_name"] == "Bedroom"
    assert data["status"] == "waiting_for_phone"
    assert data["expires_in_seconds"] > 0
    assert f"/setup.html?token={data['token']}" in data["mobile_url"]
    assert data["qr_url"] == f"/api/v1/unit-setup/sessions/{data['token']}/qr"

    # Without room_id ("Needs a home")
    unassigned_resp = client.post("/api/v1/unit-setup/sessions", json={})
    assert unassigned_resp.status_code == 201
    unassigned_data = unassigned_resp.json()
    assert unassigned_data["room_id"] is None
    assert unassigned_data["room_name"] is None
    assert unassigned_data["status"] == "waiting_for_phone"

    # Nonexistent room_id -> 404
    missing_resp = client.post("/api/v1/unit-setup/sessions", json={"room_id": 999999})
    assert missing_resp.status_code == 404


def test_unit_setup_qr_and_connect(client):
    sess = client.post("/api/v1/unit-setup/sessions", json={}).json()
    token = sess["token"]

    qr_resp = client.get(sess["qr_url"])
    assert qr_resp.status_code == 200
    assert qr_resp.headers["content-type"] == "image/png"
    assert qr_resp.content.startswith(b"\x89PNG")

    missing_qr = client.get("/api/v1/unit-setup/sessions/does-not-exist/qr")
    assert missing_qr.status_code == 404

    conn_resp = client.post(f"/api/v1/unit-setup/sessions/{token}/connect")
    assert conn_resp.status_code == 200
    assert conn_resp.json()["status"] == "creating_unit"


def test_create_unit_layouts_and_duplicate_rejection(client):
    room_id = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()["id"]

    # 1. 5-drawer 1-column medium cabinet
    sess1 = client.post("/api/v1/unit-setup/sessions", json={"room_id": room_id}).json()
    tok1 = sess1["token"]
    resp1 = client.post(
        f"/api/v1/unit-setup/sessions/{tok1}/unit",
        json={
            "name": "5 Drawer Medium Drawer Cabinet",
            "unit_kind": "cabinet",
            "compartment_count": 5,
            "compartment_label": "Drawer",
            "compartment_type": "drawer",
            "grid_cols": 1,
        },
    )
    assert resp1.status_code == 201
    body1 = resp1.json()
    assert body1["status"] == "capturing_drawers"
    assert body1["unit_name"] == "5 Drawer Medium Drawer Cabinet"
    assert body1["unit"]["parent_id"] == room_id
    assert body1["unit"]["location_type"] == "container"
    assert body1["unit"]["grid_rows"] == 5
    assert body1["unit"]["grid_cols"] == 1
    assert len(body1["drawers"]) == 5
    assert [(d["name"], d["grid_row"], d["grid_col"]) for d in body1["drawers"]] == [
        ("Drawer 1", 1, 1),
        ("Drawer 2", 2, 1),
        ("Drawer 3", 3, 1),
        ("Drawer 4", 4, 1),
        ("Drawer 5", 5, 1),
    ]

    # Reject second unit creation on same session
    dup_resp = client.post(
        f"/api/v1/unit-setup/sessions/{tok1}/unit",
        json={"name": "Another Cabinet", "compartment_count": 2},
    )
    assert dup_resp.status_code == 400

    # 2. 6-drawer 2-column dresser
    sess2 = client.post("/api/v1/unit-setup/sessions", json={"room_id": room_id}).json()
    tok2 = sess2["token"]
    resp2 = client.post(
        f"/api/v1/unit-setup/sessions/{tok2}/unit",
        json={
            "name": "Oak Dresser",
            "unit_kind": "dresser",
            "compartment_count": 6,
            "compartment_label": "Drawer",
            "compartment_type": "drawer",
            "grid_cols": 2,
        },
    )
    assert resp2.status_code == 201
    body2 = resp2.json()
    assert body2["unit"]["grid_rows"] == 3
    assert body2["unit"]["grid_cols"] == 2
    assert [(d["grid_row"], d["grid_col"]) for d in body2["drawers"]] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
        (3, 1),
        (3, 2),
    ]

    # 3. 0-compartment open unit
    sess3 = client.post("/api/v1/unit-setup/sessions", json={"room_id": room_id}).json()
    tok3 = sess3["token"]
    resp3 = client.post(
        f"/api/v1/unit-setup/sessions/{tok3}/unit",
        json={
            "name": "Open Closet",
            "unit_kind": "closet",
            "compartment_count": 0,
            "compartment_label": "Section",
            "compartment_type": "shelf",
            "grid_cols": 1,
        },
    )
    assert resp3.status_code == 201
    body3 = resp3.json()
    assert body3["unit"]["grid_rows"] is None
    assert body3["unit"]["grid_cols"] is None
    assert body3["drawers"] == []


def test_live_photo_enrichment_and_progress_updates(client):
    room_id = client.post("/api/v1/locations", json={"name": "Studio", "location_type": "room"}).json()["id"]
    sess = client.post("/api/v1/unit-setup/sessions", json={"room_id": room_id}).json()
    token = sess["token"]

    unit_res = client.post(
        f"/api/v1/unit-setup/sessions/{token}/unit",
        json={"name": "5-Drawer Cabinet", "compartment_count": 5, "grid_cols": 1},
    ).json()
    unit_id = unit_res["unit_id"]
    drawer1_id = unit_res["drawers"][0]["id"]
    drawer2_id = unit_res["drawers"][1]["id"]

    # Exterior photo
    ext_up = client.post(
        f"/api/v1/locations/{unit_id}/photos",
        files={"file": ("cabinet.jpg", _make_jpeg_bytes((80, 90, 100)), "image/jpeg")},
        data={"photo_type": "container"},
    )
    assert ext_up.status_code == 201

    # Multiple photos for Drawer 1, 1 photo for Drawer 2
    for idx in range(2):
        up = client.post(
            f"/api/v1/locations/{drawer1_id}/photos",
            files={"file": (f"d1_{idx}.jpg", _make_jpeg_bytes((120 + idx, 50, 50)), "image/jpeg")},
        )
        assert up.status_code == 201
    up2 = client.post(
        f"/api/v1/locations/{drawer2_id}/photos",
        files={"file": ("d2.jpg", _make_jpeg_bytes((50, 140, 50)), "image/jpeg")},
    )
    assert up2.status_code == 201

    # Progress update
    prog = client.patch(
        f"/api/v1/unit-setup/sessions/{token}/progress",
        json={"current_drawer_index": 2, "status": "ready_for_review"},
    )
    assert prog.status_code == 200

    status_data = client.get(f"/api/v1/unit-setup/sessions/{token}").json()
    assert status_data["status"] == "ready_for_review"
    assert status_data["current_drawer_index"] == 2
    assert status_data["unit"]["container_photo"] is not None
    assert status_data["drawers"][0]["photo_count"] == 2
    assert len(status_data["drawers"][0]["photos"]) == 2
    assert status_data["drawers"][1]["photo_count"] == 1
    assert status_data["drawers"][2]["photo_count"] == 0

    done = client.patch(
        f"/api/v1/unit-setup/sessions/{token}/progress",
        json={"status": "completed"},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "completed"


def test_unit_setup_session_expiration(client, db_session):
    from app.models.session import UnitSetupSessionRecord

    sess = client.post("/api/v1/unit-setup/sessions", json={}).json()
    token = sess["token"]
    rec = db_session.get(UnitSetupSessionRecord, token)
    rec.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).replace(tzinfo=None)
    db_session.commit()

    get_resp = client.get(f"/api/v1/unit-setup/sessions/{token}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "expired"

    assert client.post(f"/api/v1/unit-setup/sessions/{token}/connect").status_code == 410
    assert client.post(f"/api/v1/unit-setup/sessions/{token}/unit", json={"name": "Cabinet"}).status_code == 410
    assert client.patch(f"/api/v1/unit-setup/sessions/{token}/progress", json={"current_drawer_index": 1}).status_code == 410
    assert client.get(f"/api/v1/unit-setup/sessions/{token}/qr").status_code == 410


def test_unit_setup_end_to_end_ai_scan_and_manual_edit(client, monkeypatch):
    async def fake_analyze_image(image_path, provider=None, model=None, expected_count=None):
        return VisionAnalysisResponse(
            status="success",
            items=[
                DetectedItem(
                    name="Mislabelled Wrench",
                    category="Tools",
                    estimated_qty=1,
                    brief_specs="10mm",
                )
            ],
            has_vision_api=True,
            provider_used="gemini",
            model_used="test-model",
            message="Detected 1 item(s).",
        )

    monkeypatch.setattr(vision_service, "analyze_image", fake_analyze_image)

    room_id = client.post("/api/v1/locations", json={"name": "Workshop", "location_type": "room"}).json()["id"]
    sess = client.post("/api/v1/unit-setup/sessions", json={"room_id": room_id}).json()
    token = sess["token"]

    unit_data = client.post(
        f"/api/v1/unit-setup/sessions/{token}/unit",
        json={"name": "5-Drawer Cabinet", "compartment_count": 5, "grid_cols": 1},
    ).json()
    drawer1_id = unit_data["drawers"][0]["id"]

    up = client.post(
        f"/api/v1/locations/{drawer1_id}/photos",
        files={"file": ("drawer1.jpg", _make_jpeg_bytes(), "image/jpeg")},
    ).json()

    scan_resp = client.post(
        f"/api/v1/locations/{drawer1_id}/analyze-photo",
        data={"photo_id": str(up["id"])},
    )
    assert scan_resp.status_code == 200
    assert scan_resp.json()["items"][0]["name"] == "Mislabelled Wrench"

    # User corrects AI label and adds a manually remembered item
    bulk_resp = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": drawer1_id,
            "save_mode": "separate",
            "items": [
                {"name": "10mm Combination Wrench", "category": "Tools", "quantity": 1, "location_id": drawer1_id},
                {"name": "Teflon Tape", "category": "Plumbing", "quantity": 2, "location_id": drawer1_id},
            ],
        },
    )
    assert bulk_resp.status_code == 201
    assert len(bulk_resp.json()) == 2

    final_status = client.get(f"/api/v1/unit-setup/sessions/{token}").json()
    assert final_status["drawers"][0]["item_count"] == 2
    items_in_d1 = client.get(f"/api/v1/locations/{drawer1_id}/items").json()
    assert {it["name"] for it in items_in_d1} == {"10mm Combination Wrench", "Teflon Tape"}


def test_unit_setup_static_assets_served(client):
    for path in (
        "/setup.html",
        "/css/unit-setup.css",
        "/js/mobile-unit-setup.js",
        "/js/unit-setup-wizard.js",
        "/js/vision-guard.js",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"


def test_vision_guard_no_model_popup_behavioral():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert 'id="no-vision-model-modal"' in index_html
    assert 'id="no-vision-model-body"' in index_html

    app_js = (static_dir / "js" / "app.js").read_text(encoding="utf-8")
    for fn_name in ("closeNoModelModal", "retryVisionModelCheck", "continueManualFromNoModel"):
        assert fn_name in app_js

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_posix = (static_dir / "js").as_posix()
    script = f"""
    const elements = {{}};
    function makeEl(id = '') {{
      return {{
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        disabled: false,
        dataset: {{}},
        style: {{}},
        classList: {{
          _s: new Set(),
          add(...c) {{ c.forEach(x => this._s.add(x)); }},
          remove(...c) {{ c.forEach(x => this._s.delete(x)); }},
          contains(c) {{ return this._s.has(c); }},
        }},
        appendChild() {{}},
        remove() {{}},
        querySelectorAll: () => [],
      }};
    }}
    function getEl(id) {{
      if (!elements[id]) elements[id] = makeEl(id);
      return elements[id];
    }}

    globalThis.window = {{
      location: {{ host: 'localhost:8000', origin: 'http://localhost:8000' }},
    }};
    globalThis.document = {{
      getElementById: (id) => getEl(id),
      createElement: (tag) => makeEl(tag),
      querySelectorAll: () => [],
    }};

    let mockProvidersPayload = {{
      default_provider: 'local',
      providers: {{
        local: {{ label: 'Local Model (Ollama)', available: false, base_url: 'http://localhost:11434', model: 'qwen2.5vl:7b' }},
        gemini: {{ label: 'Paid: Google Gemini', available: false, model: 'gemini-2.5-flash' }},
        openai: {{ label: 'Paid: OpenAI Vision', available: false, model: 'gpt-4o-mini' }},
      }},
    }};

    globalThis.fetch = async (url) => {{
      if (String(url).includes('/api/v1/photos/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => mockProvidersPayload }};
      }}
      throw new Error(`Unexpected fetch: ${{url}}`);
    }};

    const vg = await import('file://{js_posix}/vision-guard.js');

    // 1. Unavailable local model immediately opens #no-vision-model-modal and returns ok=false
    let switchedTo = null;
    let retriedWith = null;
    const res1 = await vg.ensureVisionModelReady({{
      provider: 'local',
      onSwitchProvider: (p) => {{ switchedTo = p; }},
      onRetry: (p) => {{ retriedWith = p; }},
    }});
    if (res1.ok !== false) {{
      throw new Error('Expected ensureVisionModelReady to return ok=false when no model is available');
    }}
    const modal = getEl('no-vision-model-modal');
    if (!modal.classList.contains('open')) {{
      throw new Error('Expected #no-vision-model-modal to open immediately when no model is available');
    }}
    const bodyHtml = getEl('no-vision-model-body').innerHTML;
    if (!bodyHtml.includes('No AI vision model is running right now') || !bodyHtml.includes('qwen2.5vl:7b')) {{
      throw new Error('Expected #no-vision-model-body to explain why no model is reachable');
    }}

    // 2. When Ollama comes online and user clicks Re-check & Scan, modal closes and callbacks fire
    mockProvidersPayload = {{
      default_provider: 'local',
      providers: {{
        local: {{ label: 'Local Model (Ollama)', available: true, base_url: 'http://localhost:11434', model: 'qwen2.5vl:7b' }},
        gemini: {{ label: 'Paid: Google Gemini', available: false, model: 'gemini-2.5-flash' }},
        openai: {{ label: 'Paid: OpenAI Vision', available: false, model: 'gpt-4o-mini' }},
      }},
    }};
    await vg.retryVisionModelCheck();
    if (modal.classList.contains('open')) {{
      throw new Error('Expected #no-vision-model-modal to close after provider becomes available');
    }}
    if (switchedTo !== 'local' || retriedWith !== 'local') {{
      throw new Error(`Expected callbacks with 'local', got switchedTo=${{switchedTo}} retriedWith=${{retriedWith}}`);
    }}

    // 3. When model is already available, ensureVisionModelReady returns ok=true without opening modal
    const res2 = await vg.ensureVisionModelReady({{ provider: 'local' }});
    if (res2.ok !== true || res2.provider !== 'local' || modal.classList.contains('open')) {{
      throw new Error('Expected ensureVisionModelReady to return ok=true without opening modal when model is ready');
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"Vision guard behavioral test failed:\n{proc.stderr}"


def test_unit_setup_single_box_with_label_and_multiple_contents_photos(client):
    from pathlib import Path

    sess = client.post("/api/v1/unit-setup/sessions", json={}).json()
    token = sess["token"]

    # Create a loose single Box / Tote with physical label M2 and 0 compartments
    created = client.post(
        f"/api/v1/unit-setup/sessions/{token}/unit",
        json={
            "name": "Hardware Tote",
            "label": "M2",
            "unit_kind": "box",
            "compartment_count": 0,
            "compartment_label": "Box",
            "compartment_type": "box",
            "grid_cols": 1,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["unit"]["name"] == "Hardware Tote"
    assert body["unit"]["label"] == "M2"
    assert body["unit"]["location_type"] == "box"
    assert body["drawers"] == []

    unit_id = body["unit_id"]

    # Upload exterior photo + 2 contents photos directly onto the box
    for idx in range(3):
        up = client.post(
            f"/api/v1/locations/{unit_id}/photos",
            files={"file": (f"box_photo_{idx}.jpg", _make_jpeg_bytes(), "image/jpeg")},
        )
        assert up.status_code == 201, up.text

    # Session poll exposes all 3 live photos on unit for desktop live transfer & review
    live = client.get(f"/api/v1/unit-setup/sessions/{token}").json()
    assert len(live["unit"]["photos"]) == 3

    # Finish on phone -> ready_for_review on desktop
    done = client.patch(
        f"/api/v1/unit-setup/sessions/{token}/progress",
        json={"status": "ready_for_review"},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "ready_for_review"
    assert len(done.json()["unit"]["photos"]) == 3

    # Verify mobile-unit-setup.js supports Box / Tote preset, label input, and Step 3 contents capture for 0-drawer boxes
    mobile_js = (
        Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "mobile-unit-setup.js"
    ).read_text(encoding="utf-8")
    assert "Box / Tote" in mobile_js
    assert "setup-unit-label" in mobile_js
    assert "getCaptureTargets" in mobile_js
    assert "Add Contents Photos" in mobile_js
