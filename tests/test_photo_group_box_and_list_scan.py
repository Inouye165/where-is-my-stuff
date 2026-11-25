import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vision_service import VisionService, _build_vision_prompt


def _upload_dummy_photo(client, location_id: int, filename: str) -> dict:
    from io import BytesIO
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (40, 40), color=(90, 140, 200)).save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post(
        f"/api/v1/locations/{location_id}/photos",
        files={"file": (filename, buf.getvalue(), "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_bulk_create_box_with_label_and_grouped_source_photos(client):
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 1", "location_type": "drawer"},
    ).json()

    p1 = _upload_dummy_photo(client, drawer["id"], "loose_tool.jpg")
    p2 = _upload_dummy_photo(client, drawer["id"], "standoff_box.jpg")
    p3 = _upload_dummy_photo(client, drawer["id"], "standoff_list.jpg")

    resp = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": drawer["id"],
            "save_mode": "box",
            "assortment_name": "Brass Standoff Assortment Kit",
            "assortment_label": "M3-KIT",
            "source_photo_ids": [p2["id"], p3["id"]],
            "items": [
                {"name": "M3*6 Screws", "quantity": 40, "location_id": drawer["id"]},
                {"name": "M3*6+6 mm Brass Standoff", "quantity": 10, "location_id": drawer["id"]},
                {"name": "M3 Nuts", "quantity": 40, "location_id": drawer["id"]},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    created_items = resp.json()
    assert len(created_items) == 3

    box_id = created_items[0]["location_id"]
    assert box_id != drawer["id"]
    assert all(it["location_id"] == box_id for it in created_items)

    # New box has label and received only the 2 grouped photos
    box_resp = client.get(f"/api/v1/locations/{box_id}")
    assert box_resp.status_code == 200
    box = box_resp.json()
    assert box["name"] == "Brass Standoff Assortment Kit"
    assert box["label"] == "M3-KIT"
    assert box["location_type"] == "box"
    box_photo_ids = [p["id"] for p in box.get("photos", [])]
    assert set(box_photo_ids) == {p2["id"], p3["id"]}

    # Unselected photo stays on parent drawer
    drawer_after = client.get(f"/api/v1/locations/{drawer['id']}").json()
    drawer_photo_ids = [p["id"] for p in drawer_after.get("photos", [])]
    assert drawer_photo_ids == [p1["id"]]

    # Searching by label finds the box
    search_locs = client.get("/api/v1/locations?q=M3-KIT").json()
    assert any(loc["id"] == box_id for loc in search_locs)


@pytest.mark.asyncio
async def test_vision_service_list_prompt_and_ollama_num_predict_budget():
    prompt = _build_vision_prompt()
    lower = prompt.lower()
    assert "inventory list" in lower or "packing list" in lower
    assert "separate item" in lower

    # Verify _analyze_with_ollama sends options.num_predict >= 4096 so 30-item charts don't truncate
    fake_items = [
        {"name": f"M3*{i} Brass Standoff", "estimated_qty": 10, "bbox": [10, 10, 100, 100]}
        for i in range(1, 31)
    ]
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {
            "content": json.dumps(
                {
                    "suggested_group_name": "M3/M2.5/M2 Brass Standoff Kit",
                    "suggested_category": "Fasteners",
                    "items": fake_items,
                }
            )
        }
    }

    captured_payloads = []

    async def fake_post(url, json=None, **kwargs):
        captured_payloads.append(json)
        return fake_response

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(side_effect=fake_post)

    svc = VisionService()
    with patch("app.services.vision_service.httpx.AsyncClient", return_value=mock_client):
        result = await svc._analyze_with_ollama("AAAA", "qwen2.5vl:7b")

    assert len(captured_payloads) == 1
    opts = captured_payloads[0].get("options") or {}
    assert opts.get("num_predict", 0) >= 4096
    assert len(result.items) == 30
    assert result.suggested_group_name == "M3/M2.5/M2 Brass Standoff Kit"


def test_drawer_photo_group_as_box_and_viewer_scan_behavioral():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.BroadcastChannel = undefined;
    globalThis.URL.createObjectURL = () => 'blob:mock';
    globalThis.URL.revokeObjectURL = () => {{}};

    function makeEl(id) {{
      const classes = new Set();
      const attrs = {{}};
      return {{
        id,
        value: '',
        textContent: '',
        innerHTML: '',
        style: {{ display: '' }},
        dataset: {{}},
        disabled: false,
        open: false,
        hidden: false,
        classList: {{
          add(...c) {{ c.forEach(x => classes.add(x)); }},
          remove(...c) {{ c.forEach(x => classes.delete(x)); }},
          toggle(c, force) {{
            if (force === undefined) {{
              if (classes.has(c)) {{ classes.delete(c); return false; }}
              classes.add(c); return true;
            }}
            if (force) classes.add(c); else classes.delete(c);
            return Boolean(force);
          }},
          contains(c) {{ return classes.has(c); }},
        }},
        setAttribute(k, v) {{ attrs[k] = String(v); }},
        getAttribute(k) {{ return attrs[k] || null; }},
        removeAttribute(k) {{ delete attrs[k]; }},
        appendChild() {{}},
        addEventListener() {{}},
        querySelector() {{ return null; }},
        scrollIntoView() {{}},
        focus() {{}},
      }};
    }}

    const workspace = makeEl('app-workspace');
    const elements = {{
      'app-workspace': workspace,
      'drawer-rapid-entry-modal': makeEl('drawer-rapid-entry-modal'),
      'overall-photo-viewer-modal': makeEl('overall-photo-viewer-modal'),
      'toast-shelf': makeEl('toast-shelf'),
      'rapid-location-select': makeEl('rapid-location-select'),
      'rapid-location-pill-bar': makeEl('rapid-location-pill-bar'),
      'rapid-location-pill-name': makeEl('rapid-location-pill-name'),
      'rapid-location-field': makeEl('rapid-location-field'),
      'rapid-stage-capture': makeEl('rapid-stage-capture'),
      'rapid-stage-llm': makeEl('rapid-stage-llm'),
      'rapid-stage-review': makeEl('rapid-stage-review'),
      'rapid-photo-strip': makeEl('rapid-photo-strip'),
      'rapid-photo-filename': makeEl('rapid-photo-filename'),
      'rapid-photo-preview-wrap': makeEl('rapid-photo-preview-wrap'),
      'rapid-photo-preview': makeEl('rapid-photo-preview'),
      'rapid-scanning-banner': makeEl('rapid-scanning-banner'),
      'rapid-scanning-text': makeEl('rapid-scanning-text'),
      'rapid-ai-results': makeEl('rapid-ai-results'),
      'rapid-submit-btn': makeEl('rapid-submit-btn'),
      'rapid-ai-scan-btn': makeEl('rapid-ai-scan-btn'),
      'rapid-vision-provider': makeEl('rapid-vision-provider'),
      'rapid-vision-model': makeEl('rapid-vision-model'),
      'rapid-vision-status': makeEl('rapid-vision-status'),
      'rapid-expected-count': makeEl('rapid-expected-count'),
      'rapid-assortment-name': makeEl('rapid-assortment-name'),
      'rapid-assortment-label': makeEl('rapid-assortment-label'),
      'rapid-category-input': makeEl('rapid-category-input'),
      'rapid-items-text': makeEl('rapid-items-text'),
      'rapid-add-part-input': makeEl('rapid-add-part-input'),
      'rapid-save-mode-box': makeEl('rapid-save-mode-box'),
      'rapid-save-mode-hint': makeEl('rapid-save-mode-hint'),
      'rapid-assortment-fields': makeEl('rapid-assortment-fields'),
      'rapid-assortment-name-label': makeEl('rapid-assortment-name-label'),
      'rapid-mode-separate': makeEl('rapid-mode-separate'),
      'rapid-mode-box': makeEl('rapid-mode-box'),
      'rapid-mode-assortment': makeEl('rapid-mode-assortment'),
      'rapid-advanced-details': makeEl('rapid-advanced-details'),
      'rapid-llm-json-input': makeEl('rapid-llm-json-input'),
      'rapid-llm-prompt-preview': makeEl('rapid-llm-prompt-preview'),
      'rapid-photo-input': makeEl('rapid-photo-input'),
      'rapid-modal-title': makeEl('rapid-modal-title'),
      'rapid-modal-subtitle': makeEl('rapid-modal-subtitle'),
      'drawer-box-group-name-42': makeEl('drawer-box-group-name-42'),
      'drawer-box-group-label-42': makeEl('drawer-box-group-label-42'),
    }};
    elements['rapid-vision-provider'].value = 'local';

    globalThis.window = {{
      location: {{ host: 'localhost:8000', origin: 'http://localhost:8000', hash: '' }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
    }};
    globalThis.document = {{
      readyState: 'complete',
      activeElement: null,
      getElementById: (id) => elements[id] || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
      createElement: (tag) => makeEl(tag),
    }};

    let bulkBody = null;
    globalThis.fetch = async (url, init = {{}}) => {{
      const u = String(url);
      if (u.includes('/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => ({{ active_provider: 'local', providers: {{ local: {{ configured: true, available: true, model: 'qwen2.5vl:7b' }} }} }}) }};
      }}
      if (u.includes('/analyze-photo')) {{
        return {{
          ok: true,
          status: 200,
          json: async () => ({{
            status: 'success',
            suggested_group_name: 'Brass Standoff Kit',
            items: [
              {{ name: 'M3*6 Screws', estimated_qty: 40 }},
              {{ name: 'M3 Nuts', estimated_qty: 40 }},
            ],
          }}),
        }};
      }}
      if (u.includes('/api/v1/items/bulk')) {{
        bulkBody = JSON.parse(init.body);
        return {{ ok: true, status: 201, json: async () => [{{ id: 1, location_id: 99, name: 'M3*6 Screws' }}] }};
      }}
      if (u.includes('/api/v1/locations') || u.includes('/api/v1/items')) {{
        return {{ ok: true, status: 200, json: async () => [] }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const viewsMod = await import('file://{js_dir}/views.js');
    const modalsMod = await import('file://{js_dir}/modals.js');

    const photos = [101, 102, 103].map((id) => ({{
      id,
      photo_path: `/uploads/originals/p${{id}}.jpg`,
      thumbnail_path: `/uploads/thumbnails/p${{id}}_thumb.webp`,
      photo_type: 'container',
    }}));
    stateMod.state.locationMap.set(42, {{
      id: 42,
      name: 'Drawer 2',
      location_type: 'drawer',
      photos,
      children: [],
    }});
    stateMod.state.items = [];

    // 1. Toggle group-as-box photo selection mode
    viewsMod.toggleDrawerPhotoBoxMode(42, true);
    if (!workspace.innerHTML.includes('drawer-photo-group-bar')) {{
      throw new Error('Expected drawer-photo-group-bar to render in box photo select mode');
    }}

    // 2. Select photos 102 and 103, set name & label, and scan as box
    viewsMod.toggleDrawerPhotoBoxSelection(42, 102);
    viewsMod.toggleDrawerPhotoBoxSelection(42, 103);
    elements['drawer-box-group-name-42'].value = 'Brass Standoff Kit';
    elements['drawer-box-group-label-42'].value = 'M3-KIT';

    await viewsMod.scanSelectedPhotosAsBox(42);
    if (elements['rapid-assortment-name'].value !== 'Brass Standoff Kit') {{
      throw new Error('Expected rapid-assortment-name to be prefilled');
    }}
    if (elements['rapid-assortment-label'].value !== 'M3-KIT') {{
      throw new Error('Expected rapid-assortment-label to be prefilled');
    }}

    // 3. Submitting sends save_mode=box, assortment_label, and source_photo_ids=[102, 103]
    await modalsMod.submitRapidEntry();
    if (!bulkBody) throw new Error('Expected POST /api/v1/items/bulk to be called');
    if (bulkBody.save_mode !== 'box') throw new Error(`Expected save_mode=box, got ${{bulkBody.save_mode}}`);
    if (bulkBody.assortment_label !== 'M3-KIT') throw new Error(`Expected assortment_label=M3-KIT, got ${{bulkBody.assortment_label}}`);
    if (JSON.stringify(bulkBody.source_photo_ids) !== JSON.stringify([102, 103])) {{
      throw new Error(`Expected source_photo_ids [102,103], got ${{JSON.stringify(bulkBody.source_photo_ids)}}`);
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Drawer photo group-as-box behavioral test failed:\n{proc.stderr}"


def test_rapid_save_while_scanning_prompts_abort_or_continue_behavioral():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.BroadcastChannel = undefined;
    globalThis.URL.createObjectURL = () => 'blob:mock';
    globalThis.URL.revokeObjectURL = () => {{}};

    function makeEl(id) {{
      const classes = new Set();
      const attrs = {{}};
      return {{
        id,
        value: '',
        textContent: '',
        innerHTML: '',
        style: {{ display: '' }},
        dataset: {{}},
        disabled: false,
        open: false,
        hidden: false,
        classList: {{
          add(...c) {{ c.forEach(x => classes.add(x)); }},
          remove(...c) {{ c.forEach(x => classes.delete(x)); }},
          toggle(c, force) {{
            if (force === undefined) {{
              if (classes.has(c)) {{ classes.delete(c); return false; }}
              classes.add(c); return true;
            }}
            if (force) classes.add(c); else classes.delete(c);
            return Boolean(force);
          }},
          contains(c) {{ return classes.has(c); }},
        }},
        setAttribute(k, v) {{ attrs[k] = String(v); }},
        getAttribute(k) {{ return attrs[k] || null; }},
        removeAttribute(k) {{ delete attrs[k]; }},
        appendChild() {{}},
        addEventListener() {{}},
        querySelector() {{ return null; }},
        scrollIntoView() {{}},
        focus() {{}},
      }};
    }}

    const rapidModal = makeEl('drawer-rapid-entry-modal');
    const abortModal = makeEl('scan-abort-confirm-modal');
    const elements = {{
      'drawer-rapid-entry-modal': rapidModal,
      'scan-abort-confirm-modal': abortModal,
      'scan-abort-confirm-msg': makeEl('scan-abort-confirm-msg'),
      'toast-shelf': makeEl('toast-shelf'),
      'rapid-location-select': makeEl('rapid-location-select'),
      'rapid-location-pill-bar': makeEl('rapid-location-pill-bar'),
      'rapid-location-pill-name': makeEl('rapid-location-pill-name'),
      'rapid-location-field': makeEl('rapid-location-field'),
      'rapid-stage-capture': makeEl('rapid-stage-capture'),
      'rapid-stage-llm': makeEl('rapid-stage-llm'),
      'rapid-stage-review': makeEl('rapid-stage-review'),
      'rapid-photo-strip': makeEl('rapid-photo-strip'),
      'rapid-photo-filename': makeEl('rapid-photo-filename'),
      'rapid-photo-preview-wrap': makeEl('rapid-photo-preview-wrap'),
      'rapid-photo-preview': makeEl('rapid-photo-preview'),
      'rapid-scanning-banner': makeEl('rapid-scanning-banner'),
      'rapid-scanning-text': makeEl('rapid-scanning-text'),
      'rapid-ai-results': makeEl('rapid-ai-results'),
      'rapid-submit-btn': makeEl('rapid-submit-btn'),
      'rapid-ai-scan-btn': makeEl('rapid-ai-scan-btn'),
      'rapid-vision-provider': makeEl('rapid-vision-provider'),
      'rapid-vision-model': makeEl('rapid-vision-model'),
      'rapid-vision-status': makeEl('rapid-vision-status'),
      'rapid-expected-count': makeEl('rapid-expected-count'),
      'rapid-assortment-name': makeEl('rapid-assortment-name'),
      'rapid-assortment-label': makeEl('rapid-assortment-label'),
      'rapid-category-input': makeEl('rapid-category-input'),
      'rapid-items-text': makeEl('rapid-items-text'),
      'rapid-add-part-input': makeEl('rapid-add-part-input'),
      'rapid-save-mode-box': makeEl('rapid-save-mode-box'),
      'rapid-save-mode-hint': makeEl('rapid-save-mode-hint'),
      'rapid-assortment-fields': makeEl('rapid-assortment-fields'),
      'rapid-assortment-name-label': makeEl('rapid-assortment-name-label'),
      'rapid-mode-separate': makeEl('rapid-mode-separate'),
      'rapid-mode-box': makeEl('rapid-mode-box'),
      'rapid-mode-assortment': makeEl('rapid-mode-assortment'),
      'rapid-advanced-details': makeEl('rapid-advanced-details'),
      'rapid-llm-json-input': makeEl('rapid-llm-json-input'),
      'rapid-llm-prompt-preview': makeEl('rapid-llm-prompt-preview'),
      'rapid-photo-input': makeEl('rapid-photo-input'),
      'rapid-modal-title': makeEl('rapid-modal-title'),
      'rapid-modal-subtitle': makeEl('rapid-modal-subtitle'),
    }};
    elements['rapid-vision-provider'].value = 'local';

    globalThis.window = {{
      location: {{ host: 'localhost:8000', origin: 'http://localhost:8000', hash: '' }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
    }};
    globalThis.document = {{
      readyState: 'complete',
      activeElement: null,
      getElementById: (id) => elements[id] || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
      createElement: (tag) => makeEl(tag),
    }};

    let resolveScan;
    let lastSignal = null;
    let bulkCalls = 0;
    globalThis.fetch = async (url, init = {{}}) => {{
      const u = String(url);
      if (u.includes('/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => ({{ active_provider: 'local', providers: {{ local: {{ configured: true, available: true, model: 'qwen2.5vl:7b' }} }} }}) }};
      }}
      if (u.includes('/analyze-photo')) {{
        lastSignal = init.signal || null;
        return await new Promise((res, rej) => {{
          resolveScan = res;
          if (lastSignal) {{
            lastSignal.addEventListener('abort', () => {{
              const err = new Error('Aborted');
              err.name = 'AbortError';
              rej(err);
            }});
          }}
        }});
      }}
      if (u.includes('/api/v1/items/bulk')) {{
        bulkCalls += 1;
        return {{ ok: true, status: 201, json: async () => [{{ id: 1, location_id: 7, name: 'Manual Item' }}] }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const guardMod = await import('file://{js_dir}/unsaved-guard.js');
    const modalsMod = await import('file://{js_dir}/modals.js');

    if (typeof guardMod.resolveScanAbortPrompt !== 'function') {{
      throw new Error('resolveScanAbortPrompt is not exported from unsaved-guard.js');
    }}

    stateMod.state.locationMap.set(7, {{
      id: 7,
      name: 'Drawer 7',
      location_type: 'drawer',
      photos: [{{ id: 55, photo_path: '/uploads/originals/p55.jpg' }}],
      children: [],
    }});

    // 1. Start scan; user clicks Save prematurely and chooses "Continue Scan"
    await modalsMod.openRapidEntryModal(7, {{ photoIds: [55], autoScan: false }});
    const scan1 = modalsMod.runRapidAiScan();
    await new Promise(r => setTimeout(r, 20));

    const saveAttempt1 = modalsMod.submitRapidEntry();
    await new Promise(r => setTimeout(r, 10));
    if (!abortModal.classList.contains('open')) {{
      throw new Error('Expected #scan-abort-confirm-modal to open when saving during active scan');
    }}
    if (bulkCalls !== 0) {{
      throw new Error('Expected no save API call before user confirms abort');
    }}

    guardMod.resolveScanAbortPrompt('continue');
    await saveAttempt1;
    if (abortModal.classList.contains('open')) {{
      throw new Error('Expected #scan-abort-confirm-modal to close after Continue Scan');
    }}
    if (rapidModal.dataset.scanning !== 'true') {{
      throw new Error('Expected scan to continue running when user chooses Continue Scan');
    }}
    if (lastSignal && lastSignal.aborted) {{
      throw new Error('Expected scan AbortController NOT to be aborted when user chooses Continue Scan');
    }}

    // Finish scan 1 normally and verify results render
    resolveScan({{
      ok: true,
      status: 200,
      json: async () => ({{ status: 'success', items: [{{ name: 'M3 Standoff', estimated_qty: 10 }}] }}),
    }});
    await scan1;
    if (rapidModal.dataset.scanning !== 'false') {{
      throw new Error('Expected scanning to finish cleanly after Continue Scan');
    }}

    // 2. Start another scan; user clicks Save and chooses "Abort Scan & Save"
    elements['rapid-items-text'].value = '2x Spare Nut';
    const scan2 = modalsMod.runRapidAiScan();
    await new Promise(r => setTimeout(r, 20));
    const signal2 = lastSignal;

    const saveAttempt2 = modalsMod.submitRapidEntry();
    await new Promise(r => setTimeout(r, 10));
    if (!abortModal.classList.contains('open')) {{
      throw new Error('Expected #scan-abort-confirm-modal to open on second premature save');
    }}
    guardMod.resolveScanAbortPrompt('abort');
    await saveAttempt2;
    await scan2;

    if (!signal2 || !signal2.aborted) {{
      throw new Error('Expected scan AbortController to be aborted when user confirms Abort Scan');
    }}
    if (bulkCalls !== 1) {{
      throw new Error(`Expected bulk save to proceed after abort confirmation, got ${{bulkCalls}}`);
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Rapid save-while-scanning prompt test failed:\n{proc.stderr}"
