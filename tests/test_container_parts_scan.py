from io import BytesIO
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from PIL import Image


def _make_jpeg_bytes(color=(180, 140, 60), size=(120, 90)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_vision_analysis_extracts_assortment_parts_and_group_metadata(client):
    from app.services.vision_service import _VISION_PROMPT

    prompt_low = _VISION_PROMPT.lower()
    assert "assort" in prompt_low or "compartment" in prompt_low
    assert "suggested_group_name" in _VISION_PROMPT

    drawer = client.post(
        "/api/v1/locations", json={"name": "Drawer 7", "location_type": "drawer"}
    ).json()
    photo = client.post(
        f"/api/v1/locations/{drawer['id']}/photos",
        files={"file": ("transistor_kit.jpg", _make_jpeg_bytes(), "image/jpeg")},
    ).json()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "content": (
                '{'
                '"suggested_group_name": "Assorted NPN/PNP Transistors", '
                '"suggested_category": "Electronic Components", '
                '"items": ['
                '{"name": "2N2222 NPN Transistor", "part_number": "2N2222", "category": "Transistors", "estimated_qty": 20, "brief_specs": "TO-92 40V 600mA"}, '
                '{"name": "BC547 NPN Transistor", "part_number": "BC547", "category": "Transistors", "estimated_qty": 20, "brief_specs": "TO-92 45V 100mA"}, '
                '{"name": "2N3906 PNP Transistor", "part_number": "2N3906", "category": "Transistors", "estimated_qty": 15, "brief_specs": "TO-92 40V 200mA"}'
                ']'
                '}'
            )
        }
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        res = client.post(
            f"/api/v1/locations/{drawer['id']}/analyze-photo",
            data={"photo_id": str(photo["id"]), "provider": "local"},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "success"
    assert body["suggested_group_name"] == "Assorted NPN/PNP Transistors"
    assert body["suggested_category"] == "Electronic Components"
    assert len(body["items"]) == 3
    assert body["items"][0]["name"] == "2N2222 NPN Transistor"
    assert body["items"][0]["part_number"] == "2N2222"
    assert body["items"][0]["estimated_qty"] == 20


def test_bulk_create_separate_mode_with_edited_and_added_parts(client):
    drawer = client.post(
        "/api/v1/locations", json={"name": "Tool Drawer", "location_type": "drawer"}
    ).json()

    payload = {
        "location_id": drawer["id"],
        "save_mode": "separate",
        "category": "Hand Tools",
        "items": [
            {
                "name": "#2 Phillips Screwdriver",
                "quantity": 2,
                "category": "Screwdrivers",
                "description": "6 in magnetic tip",
                "serial_or_part_number": "PH2-6",
                "item_photo": "/uploads/originals/ph2_crop.jpg",
            },
            {
                "name": "Precision Flathead Screwdriver",
                "quantity": 1,
                "description": "2.5mm slotted",
            },
        ],
        "raw_lines": "3x Torx T10 Screwdriver",
    }

    res = client.post("/api/v1/items/bulk", json=payload)
    assert res.status_code == 201, res.text
    created = res.json()
    assert len(created) == 3

    by_name = {it["name"]: it for it in created}
    assert by_name["#2 Phillips Screwdriver"]["quantity"] == 2
    assert by_name["#2 Phillips Screwdriver"]["category"] == "Screwdrivers"
    assert by_name["#2 Phillips Screwdriver"]["serial_or_part_number"] == "PH2-6"
    assert by_name["#2 Phillips Screwdriver"]["item_photo"] == "/uploads/originals/ph2_crop.jpg"
    assert by_name["Precision Flathead Screwdriver"]["category"] == "Hand Tools"
    assert by_name["Torx T10 Screwdriver"]["quantity"] == 3
    assert by_name["Torx T10 Screwdriver"]["category"] == "Hand Tools"


def test_bulk_create_assortment_mode_creates_searchable_kit_item(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Tall 9 drawer storage cabinet", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 7", "location_type": "drawer", "parent_id": cabinet["id"]},
    ).json()

    payload = {
        "location_id": drawer["id"],
        "save_mode": "assortment",
        "assortment_name": "Assorted Transistors Kit",
        "assortment_photo": "/uploads/originals/transistor_box.jpg",
        "category": "Electronic Components",
        "items": [
            {
                "name": "2N2222 NPN Transistor",
                "quantity": 25,
                "serial_or_part_number": "2N2222",
                "description": "TO-92 40V 600mA",
            },
            {
                "name": "BC547 NPN Transistor",
                "quantity": 20,
                "serial_or_part_number": "BC547",
                "description": "TO-92 low noise",
            },
        ],
        "raw_lines": "10x TIP120 Darlington",
    }

    res = client.post("/api/v1/items/bulk", json=payload)
    assert res.status_code == 201, res.text
    created = res.json()
    assert len(created) == 1

    kit = created[0]
    assert kit["name"] == "Assorted Transistors Kit"
    assert kit["category"] == "Electronic Components"
    assert kit["quantity"] == 1
    assert kit["unit"] == "kit"
    assert kit["item_photo"] == "/uploads/originals/transistor_box.jpg"
    assert "Parts List:" in kit["description"]
    assert "25x 2N2222 NPN Transistor" in kit["description"]
    assert "20x BC547 NPN Transistor" in kit["description"]
    assert "10x TIP120 Darlington" in kit["description"]

    # Searching any individual part inside the assortment finds the kit and its drawer path
    for query_term in ("2N2222", "BC547", "TIP120", "Darlington"):
        s_res = client.get("/api/v1/search", params={"q": query_term})
        assert s_res.status_code == 200
        hits = s_res.json()["items"]
        assert any(h["id"] == kit["id"] for h in hits), f"Expected kit in search results for '{query_term}'"
        matched = next(h for h in hits if h["id"] == kit["id"])
        assert matched["location_full_path"] == "Office > Tall 9 drawer storage cabinet > Drawer 7"


def test_frontend_editable_parts_list_and_views_entry_points():
    root = Path(__file__).resolve().parent.parent
    views_src = (root / "app" / "static" / "js" / "views.js").read_text(encoding="utf-8")
    modals_src = (root / "app" / "static" / "js" / "modals.js").read_text(encoding="utf-8")
    html_src = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")

    # Drawer quick-add bar and Storage Unit view both expose AI parts scan entry points
    assert "openRapidEntryModal" in views_src
    assert "Scan Items" in views_src or "Scan Parts" in views_src

    # Editable parts list & save mode choice in Scan modal
    assert "rapid-save-mode" in html_src or "setRapidSaveMode" in modals_src
    assert "addPartToRapidList" in modals_src
    assert "updateRapidDetectedItemField" in modals_src
    assert "removeRapidDetectedItem" in modals_src

    # Item detail view supports viewing & editing/adding to an item's parts list
    assert "saveItemPartsList" in views_src


def test_frontend_parts_list_editor_and_save_modes_behavioral():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.URL.createObjectURL = () => 'blob:test';
    globalThis.URL.revokeObjectURL = () => {{}};
    globalThis.BroadcastChannel = undefined;
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.window = {{
      location: {{ host: 'localhost:8000', hash: '#/', origin: 'http://localhost:8000' }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
      confirm: () => true,
    }};
    globalThis.requestAnimationFrame = (fn) => fn();
    Object.defineProperty(globalThis, 'navigator', {{
      value: {{ userAgent: 'Mozilla/5.0 Windows NT 10.0', maxTouchPoints: 0 }},
      configurable: true,
    }});
    globalThis.window.navigator = globalThis.navigator;

    function makeEl(id, extra = {{}}) {{
      const classes = new Set();
      return {{
        id,
        value: '',
        textContent: '',
        innerHTML: '',
        disabled: false,
        dataset: {{}},
        style: {{}},
        scrollTop: 0,
        classList: {{
          add: (...c) => c.forEach(x => classes.add(x)),
          remove: (...c) => c.forEach(x => classes.delete(x)),
          contains: (c) => classes.has(c),
          toggle: (c, f) => {{
            if (f === undefined) {{
              if (classes.has(c)) {{ classes.delete(c); return false; }}
              classes.add(c); return true;
            }}
            if (f) classes.add(c); else classes.delete(c);
            return Boolean(f);
          }},
        }},
        scrollIntoView() {{}},
        appendChild() {{}},
        focus() {{}},
        select() {{}},
        click() {{}},
        ...extra,
      }};
    }}

    const elements = {{
      'drawer-rapid-entry-modal': makeEl('drawer-rapid-entry-modal'),
      'rapid-location-pill-bar': makeEl('rapid-location-pill-bar'),
      'rapid-location-pill-name': makeEl('rapid-location-pill-name'),
      'rapid-location-field': makeEl('rapid-location-field'),
      'rapid-location-select': makeEl('rapid-location-select', {{ value: '51' }}),
      'rapid-stage-capture': makeEl('rapid-stage-capture'),
      'rapid-stage-review': makeEl('rapid-stage-review'),
      'rapid-existing-photos-btn': makeEl('rapid-existing-photos-btn'),
      'rapid-quiet-ai-btn': makeEl('rapid-quiet-ai-btn'),
      'rapid-photo-strip': makeEl('rapid-photo-strip'),
      'rapid-photo-input': makeEl('rapid-photo-input'),
      'rapid-photo-filename': makeEl('rapid-photo-filename'),
      'rapid-photo-preview-wrap': makeEl('rapid-photo-preview-wrap'),
      'rapid-scanning-banner': makeEl('rapid-scanning-banner'),
      'rapid-scanning-text': makeEl('rapid-scanning-text'),
      'rapid-vision-provider': makeEl('rapid-vision-provider', {{ value: 'local' }}),
      'rapid-vision-model': makeEl('rapid-vision-model', {{ value: '' }}),
      'rapid-vision-status': makeEl('rapid-vision-status'),
      'rapid-ai-scan-btn': makeEl('rapid-ai-scan-btn'),
      'rapid-ai-results': makeEl('rapid-ai-results'),
      'rapid-add-part-input': makeEl('rapid-add-part-input', {{ value: '' }}),
      'rapid-save-mode-box': makeEl('rapid-save-mode-box'),
      'rapid-mode-separate': makeEl('rapid-mode-separate'),
      'rapid-mode-assortment': makeEl('rapid-mode-assortment'),
      'rapid-save-mode-hint': makeEl('rapid-save-mode-hint'),
      'rapid-assortment-fields': makeEl('rapid-assortment-fields'),
      'rapid-assortment-name': makeEl('rapid-assortment-name', {{ value: '' }}),
      'rapid-advanced-details': makeEl('rapid-advanced-details', {{ open: true }}),
      'rapid-items-text': makeEl('rapid-items-text', {{ value: '' }}),
      'rapid-category-input': makeEl('rapid-category-input', {{ value: '' }}),
      'rapid-category-datalist': makeEl('rapid-category-datalist'),
      'rapid-submit-btn': makeEl('rapid-submit-btn'),
      'item-location-select': makeEl('item-location-select'),
      'storage-parent-select': makeEl('storage-parent-select'),
      'app-workspace': makeEl('app-workspace'),
      'toast-shelf': makeEl('toast-shelf'),
    }};

    globalThis.document = {{
      readyState: 'complete',
      documentElement: {{ setAttribute() {{}} }},
      activeElement: null,
      fullscreenElement: null,
      getElementById: (id) => elements[id] || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
      createElement: (tag) => makeEl(tag),
    }};

    const bulkPayloads = [];
    const patchPayloads = [];
    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      if (u.endsWith('/vision-providers')) {{
        return {{
          ok: true,
          status: 200,
          json: async () => ({{ default_provider: 'local', providers: {{ local: {{ available: true, model: 'qwen2.5vl:7b' }} }} }}),
        }};
      }}
      if (u.includes('/analyze-photo')) {{
        return {{
          ok: true,
          status: 200,
          json: async () => ({{
            status: 'success',
            suggested_group_name: 'Assorted NPN/PNP Transistors',
            suggested_category: 'Electronic Components',
            message: 'Detected 2 items.',
            items: [
              {{ name: '2N2222 Transistor', part_number: '2N2222', category: 'Transistors', estimated_qty: 10, brief_specs: 'NPN TO-92', crop_photo_path: '/uploads/originals/c1.jpg' }},
              {{ name: 'Misread Dust Speck', category: 'Misc', estimated_qty: 1 }},
            ],
          }}),
        }};
      }}
      if (u.endsWith('/api/v1/items/bulk')) {{
        const body = JSON.parse(opts.body);
        bulkPayloads.push(body);
        if (body.save_mode === 'assortment') {{
          return {{ ok: true, status: 201, json: async () => [{{ id: 201, name: body.assortment_name || 'Kit' }}] }};
        }}
        return {{ ok: true, status: 201, json: async () => (body.items || []).map((it, idx) => ({{ id: 100 + idx, name: it.name }})) }};
      }}
      if (u.startsWith('/api/v1/items/') && opts.method === 'PATCH') {{
        const body = JSON.parse(opts.body);
        patchPayloads.push({{ url: u, body }});
        return {{ ok: true, status: 200, json: async () => ({{ id: 201, ...body }}) }};
      }}
      return {{ ok: true, status: 200, json: async () => [] }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const modals = await import('file://{js_dir}/modals.js');
    const views = await import('file://{js_dir}/views.js');

    stateMod.state.locationMap.set(51, {{
      id: 51,
      name: 'Drawer 7',
      location_type: 'drawer',
      full_path: 'Office > Cabinet > Drawer 7',
      photos: [{{ id: 12, photo_path: '/uploads/originals/drawer7.jpg' }}],
    }});

    // 0. Progressive disclosure: opening without photo options lands on clean Stage 1 (Capture)
    await modals.openRapidEntryModal(51);
    if (elements['rapid-stage-capture'].style.display !== 'flex' || elements['rapid-stage-review'].style.display !== 'none') {{
      throw new Error('Expected Stage 1 Capture view on initial open without photo');
    }}
    if (elements['rapid-location-pill-bar'].style.display !== 'flex' || elements['rapid-location-field'].style.display !== 'none') {{
      throw new Error('Expected compact location pill bar and collapsed dropdown when location is known');
    }}
    if (elements['rapid-submit-btn'].style.display !== 'none') {{
      throw new Error('Expected Save button hidden on empty Stage 1');
    }}
    if (elements['rapid-advanced-details'].open !== false) {{
      throw new Error('Expected Advanced options collapsed by default');
    }}

    // 1. Open modal with autoScan -> switches to Stage 2 & AI populates detected list + suggested assortment name
    await modals.openRapidEntryModal(51, {{ photoId: 12, autoScan: true }});
    if (elements['rapid-stage-review'].style.display !== 'flex') {{
      throw new Error('Expected Stage 2 Review view after photo scan');
    }}
    if (elements['rapid-save-mode-box'].style.display === 'none') {{
      throw new Error('Expected Save Mode box visible when 2+ items are checked');
    }}
    if (elements['rapid-assortment-name'].value !== 'Assorted NPN/PNP Transistors') {{
      throw new Error(`Expected suggested group name prefilled, got '${{elements['rapid-assortment-name'].value}}'`);
    }}

    // 2. Remove false positive -> with only 1 item left, Save Mode box hides automatically!
    modals.removeRapidDetectedItem(1);
    if (elements['rapid-save-mode-box'].style.display !== 'none') {{
      throw new Error('Expected Save Mode box to auto-hide when only 1 item is checked');
    }}
    // Expand inline details for row 0, edit qty & name, then paste multi-line parts
    modals.toggleRapidItemDetails(0);
    if (!elements['rapid-ai-results'].innerHTML.includes('ai-editable-sub')) {{
      throw new Error('Expected toggleRapidItemDetails(0) to expand inline Part # and Specs inputs');
    }}
    modals.updateRapidDetectedItemField(0, 'estimated_qty', '25');
    modals.updateRapidDetectedItemField(0, 'name', '2N2222A NPN Transistor');
    modals.handleRapidAddPartPaste({{
      preventDefault() {{}},
      clipboardData: {{ getData: () => '20x BC547 — TO-92\\n15x 2N3906 [2N3906]' }},
    }});
    if (elements['rapid-save-mode-box'].style.display === 'none') {{
      throw new Error('Expected Save Mode box to reappear once 3 items are in list');
    }}

    // 3. Save in assortment mode
    modals.setRapidSaveMode('assortment');
    await modals.submitRapidEntry();

    if (bulkPayloads.length !== 1) {{
      throw new Error(`Expected 1 bulk POST, got ${{bulkPayloads.length}}`);
    }}
    const kitReq = bulkPayloads[0];
    if (kitReq.save_mode !== 'assortment' || kitReq.assortment_name !== 'Assorted NPN/PNP Transistors' || kitReq.items.length !== 3) {{
      throw new Error(`Unexpected assortment payload: ${{JSON.stringify(kitReq)}}`);
    }}
    if (kitReq.items[0].name !== '2N2222A NPN Transistor' || kitReq.items[0].quantity !== 25) {{
      throw new Error(`Edits to detected item were not preserved: ${{JSON.stringify(kitReq.items[0])}}`);
    }}
    if (kitReq.items[2].serial_or_part_number !== '2N3906') {{
      throw new Error(`Bracketed part number not parsed: ${{JSON.stringify(kitReq.items[2])}}`);
    }}

    // 4. Post-save: edit & add to an existing assortment item's parts list in Item Detail View
    stateMod.state.items = [{{
      id: 201,
      name: 'Assorted NPN/PNP Transistors',
      location_id: 51,
      quantity: 1,
      unit: 'kit',
      description: 'Parts List:\\n• 25x 2N2222A NPN Transistor [2N2222] — NPN TO-92\\n• 20x BC547 — TO-92',
      tags: '2N2222, BC547',
      last_seen_at: new Date().toISOString(),
    }}];
    elements['save-item-parts-btn-201'] = makeEl('save-item-parts-btn-201');
    elements['item-part-quick-add-201'] = makeEl('item-part-quick-add-201', {{ value: '10x TIP120 Darlington' }});

    views.toggleItemPartsEditor(201, true);
    views.updateItemPartField(201, 0, 'qty', '30');
    await views.saveItemPartsList(201);

    if (patchPayloads.length !== 1) {{
      throw new Error(`Expected 1 PATCH to item 201, got ${{patchPayloads.length}}`);
    }}
    const patched = patchPayloads[0].body;
    if (!patched.description.includes('30x 2N2222A NPN Transistor') || !patched.description.includes('10x TIP120 Darlington')) {{
      throw new Error(`Updated parts list missing expected entries: ${{patched.description}}`);
    }}
    if (!patched.tags.includes('TIP120')) {{
      throw new Error(`Updated tags missing newly added part: ${{patched.tags}}`);
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"Behavioral parts list test failed:\n{proc.stderr}"

