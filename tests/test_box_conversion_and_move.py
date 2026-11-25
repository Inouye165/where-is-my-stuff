from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from PIL import Image
import pytest

from app.services.photo_service import photo_service


def _upload_sample_photo(client, color=(40, 110, 190)) -> str:
    buf = BytesIO()
    Image.new("RGB", (320, 240), color=color).save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post(
        "/api/v1/photos/upload",
        files={"file": ("m2_box.jpg", buf, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["original_path"]


def test_convert_item_to_box_preserves_photo_and_supports_contents_and_move(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "5 Drawer Cabinet", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer1 = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 1", "location_type": "drawer", "parent_id": cabinet["id"], "grid_row": 1, "grid_col": 1},
    ).json()
    drawer2 = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 2", "location_type": "drawer", "parent_id": cabinet["id"], "grid_row": 2, "grid_col": 1},
    ).json()

    photo_url = _upload_sample_photo(client, (180, 60, 40))
    photo_file = photo_service.resolve_safe_upload_path(photo_url)
    assert photo_file.exists()

    # Original item detected by AI (a plastic container labeled M2)
    item = client.post(
        "/api/v1/items",
        json={
            "name": "Small assorted tools",
            "description": "Screwdriver with 'M2' marking",
            "location_id": drawer1["id"],
            "item_photo": photo_url,
            "quantity": 1,
        },
    ).json()

    # Convert item -> Box / Container inside Drawer 1
    conv_resp = client.post(
        f"/api/v1/items/{item['id']}/convert-to-box",
        json={"box_name": "M2 Plastic Container", "keep_item_inside": False},
    )
    assert conv_resp.status_code == 201, conv_resp.text
    box = conv_resp.json()
    assert box["name"] == "M2 Plastic Container"
    assert box["location_type"] == "box"
    assert box["parent_id"] == drawer1["id"]
    assert box["container_photo"] == photo_url
    assert len(box["photos"]) == 1
    assert box["photos"][0]["photo_path"] == photo_url
    assert box["full_path"] == "Office > 5 Drawer Cabinet > Drawer 1 > M2 Plastic Container"

    # Original placeholder item removed, but physical photo file preserved on disk!
    assert client.get(f"/api/v1/items/{item['id']}").status_code == 404
    assert photo_file.exists()

    # Missing item -> 404
    assert client.post("/api/v1/items/999999/convert-to-box", json={}).status_code == 404

    # Add inside photo and items into the new Box
    buf2 = BytesIO()
    Image.new("RGB", (300, 220), color=(30, 150, 80)).save(buf2, format="JPEG")
    buf2.seek(0)
    up_inside = client.post(
        f"/api/v1/locations/{box['id']}/photos",
        files={"file": ("inside_m2.jpg", buf2, "image/jpeg")},
    )
    assert up_inside.status_code == 201

    bulk = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": box["id"],
            "save_mode": "separate",
            "items": [
                {"name": "M2 Phillips Screwdriver", "quantity": 1, "location_id": box["id"]},
                {"name": "Precision Tweezers", "quantity": 1, "location_id": box["id"]},
            ],
        },
    )
    assert bulk.status_code == 201
    created_items = bulk.json()
    assert len(created_items) == 2

    # Move the Box from Drawer 1 to Drawer 2
    move_resp = client.patch(
        f"/api/v1/locations/{box['id']}",
        json={"parent_id": drawer2["id"]},
    )
    assert move_resp.status_code == 200, move_resp.text
    moved_box = move_resp.json()
    assert moved_box["parent_id"] == drawer2["id"]
    assert moved_box["full_path"] == "Office > 5 Drawer Cabinet > Drawer 2 > M2 Plastic Container"

    # Items inside the Box now reflect Drawer 2 in detail and search, and counts rolled over
    item_detail = client.get(f"/api/v1/items/{created_items[0]['id']}").json()
    assert item_detail["location_full_path"] == "Office > 5 Drawer Cabinet > Drawer 2 > M2 Plastic Container"

    search_res = client.get("/api/v1/search", params={"q": "Phillips"}).json()
    assert search_res["total"] == 1
    assert search_res["items"][0]["location_full_path"] == "Office > 5 Drawer Cabinet > Drawer 2 > M2 Plastic Container"

    d1_after = client.get(f"/api/v1/locations/{drawer1['id']}").json()
    d2_after = client.get(f"/api/v1/locations/{drawer2['id']}").json()
    assert d1_after["total_item_count"] == 0
    assert d2_after["total_item_count"] == 2

    # Circular move (box into itself) rejected
    self_move = client.patch(f"/api/v1/locations/{box['id']}", json={"parent_id": box["id"]})
    assert self_move.status_code == 400


def test_convert_item_to_box_keep_item_inside_and_location_create_photo_sync(client):
    room = client.post("/api/v1/locations", json={"name": "Workshop", "location_type": "room"}).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer A", "location_type": "drawer", "parent_id": room["id"]},
    ).json()

    photo_url = _upload_sample_photo(client, (90, 90, 200))

    # Creating a box directly via POST /locations with container_photo syncs LocationPhoto
    direct_box = client.post(
        "/api/v1/locations",
        json={
            "name": "M2 Container",
            "location_type": "box",
            "parent_id": drawer["id"],
            "container_photo": photo_url,
        },
    )
    assert direct_box.status_code == 201, direct_box.text
    dbox_data = direct_box.json()
    assert dbox_data["container_photo"] == photo_url
    assert len(dbox_data["photos"]) == 1
    assert dbox_data["photos"][0]["photo_path"] == photo_url

    # Convert with keep_item_inside=True moves the item inside the new box with audit trail
    item = client.post(
        "/api/v1/items",
        json={"name": "Screwdriver M2", "location_id": drawer["id"], "item_photo": photo_url},
    ).json()
    conv = client.post(
        f"/api/v1/items/{item['id']}/convert-to-box",
        json={"box_name": "Tool Case M2", "keep_item_inside": True},
    )
    assert conv.status_code == 201
    new_box = conv.json()
    item_after = client.get(f"/api/v1/items/{item['id']}").json()
    assert item_after["location_id"] == new_box["id"]
    assert any(log["action"] == "moved" for log in item_after["audit_logs"])


def test_frontend_box_conversion_per_row_and_move_behavioral():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    js_dir = (static_dir / "js").as_posix()

    app_js = (static_dir / "js" / "app.js").read_text(encoding="utf-8")
    for sym in (
        "openConvertItemToBoxModal",
        "submitConvertItemToBox",
        "openMoveBoxModal",
        "submitMoveBox",
        "toggleRapidItemAsBox",
    ):
        assert sym in app_js, f"Expected {sym} exported in app.js"

    script = f"""
    globalThis.URL.createObjectURL = () => 'blob:test';
    globalThis.URL.revokeObjectURL = () => {{}};
    globalThis.BroadcastChannel = undefined;
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.requestAnimationFrame = (fn) => fn && fn();

    function makeEl(id, extra = {{}}) {{
      const classes = new Set();
      return {{
        id,
        value: '',
        checked: false,
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
            const on = f === undefined ? !classes.has(c) : Boolean(f);
            if (on) classes.add(c); else classes.delete(c);
            return on;
          }},
        }},
        scrollIntoView() {{}},
        appendChild() {{}},
        remove() {{}},
        focus() {{}},
        select() {{}},
        click() {{}},
        ...extra,
      }};
    }}

    const elements = {{
      'app-workspace': makeEl('app-workspace'),
      'toast-shelf': makeEl('toast-shelf'),
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
      'rapid-mode-box': makeEl('rapid-mode-box'),
      'rapid-mode-assortment': makeEl('rapid-mode-assortment'),
      'rapid-save-mode-hint': makeEl('rapid-save-mode-hint'),
      'rapid-assortment-fields': makeEl('rapid-assortment-fields'),
      'rapid-assortment-name': makeEl('rapid-assortment-name', {{ value: '' }}),
      'rapid-advanced-details': makeEl('rapid-advanced-details'),
      'rapid-items-text': makeEl('rapid-items-text', {{ value: '' }}),
      'rapid-category-input': makeEl('rapid-category-input', {{ value: '' }}),
      'rapid-category-datalist': makeEl('rapid-category-datalist'),
      'rapid-submit-btn': makeEl('rapid-submit-btn'),
      'item-location-select': makeEl('item-location-select'),
      'storage-parent-select': makeEl('storage-parent-select'),
      'convert-to-box-modal': makeEl('convert-to-box-modal'),
      'convert-box-item-id': makeEl('convert-box-item-id'),
      'convert-box-name-input': makeEl('convert-box-name-input'),
      'convert-box-keep-item': makeEl('convert-box-keep-item'),
      'convert-box-preview': makeEl('convert-box-preview'),
      'convert-box-submit-btn': makeEl('convert-box-submit-btn'),
      'move-box-modal': makeEl('move-box-modal'),
      'move-box-id': makeEl('move-box-id'),
      'move-box-title': makeEl('move-box-title'),
      'move-box-current-path': makeEl('move-box-current-path'),
      'move-box-dest-select': makeEl('move-box-dest-select'),
      'move-box-save-btn': makeEl('move-box-save-btn'),
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
    globalThis.window = {{
      location: {{ host: 'localhost:8000', hash: '#/', origin: 'http://localhost:8000' }},
      history: {{ pushState() {{}} }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
      confirm: () => true,
    }};
    Object.defineProperty(globalThis, 'navigator', {{
      value: {{ userAgent: 'Mozilla/5.0 Windows NT 10.0', maxTouchPoints: 0 }},
      configurable: true,
    }});
    globalThis.window.navigator = globalThis.navigator;

    const createdLocations = [];
    const bulkPayloads = [];
    const patchedLocations = [];
    let mockTree = [];

    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      if (u.endsWith('/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => ({{ default_provider: 'local', providers: {{}} }}) }};
      }}
      if (u.includes('/locations/tree')) {{
        return {{ ok: true, status: 200, json: async () => mockTree }};
      }}
      if (u.endsWith('/api/v1/locations') && opts.method === 'POST') {{
        const body = JSON.parse(opts.body);
        createdLocations.push(body);
        return {{ ok: true, status: 201, json: async () => ({{ id: 901, ...body }}) }};
      }}
      if (u.includes('/api/v1/locations/') && opts.method === 'PATCH') {{
        const body = JSON.parse(opts.body);
        patchedLocations.push({{ url: u, body }});
        return {{ ok: true, status: 200, json: async () => ({{ id: 88, name: 'M2 Box', ...body }}) }};
      }}
      if (u.endsWith('/api/v1/items/bulk')) {{
        const body = JSON.parse(opts.body);
        bulkPayloads.push(body);
        return {{ ok: true, status: 201, json: async () => (body.items || []).map((it, i) => ({{ id: 700 + i, ...it }})) }};
      }}
      if (u.includes('/api/v1/items')) {{
        return {{ ok: true, status: 200, json: async () => [] }};
      }}
      return {{ ok: true, status: 200, json: async () => [] }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const modals = await import('file://{js_dir}/modals.js');
    const boxActions = await import('file://{js_dir}/box-actions.js');

    const nestedChildBox = {{ id: 89, name: 'Inner Pouch', location_type: 'box', parent_id: 88, full_path: 'Office > Cabinet > Drawer 1 > M2 Box > Inner Pouch', children: [] }};
    const m2Box = {{ id: 88, name: 'M2 Box', location_type: 'box', parent_id: 51, full_path: 'Office > Cabinet > Drawer 1 > M2 Box', children: [nestedChildBox] }};
    const drawer1 = {{ id: 51, name: 'Drawer 1', location_type: 'drawer', parent_id: 10, full_path: 'Office > Cabinet > Drawer 1', children: [m2Box] }};
    const drawer2 = {{ id: 52, name: 'Drawer 2', location_type: 'drawer', parent_id: 10, full_path: 'Office > Cabinet > Drawer 2', children: [] }};
    const cabinet = {{ id: 10, name: 'Cabinet', location_type: 'container', parent_id: 1, full_path: 'Office > Cabinet', children: [drawer1, drawer2] }};
    const room = {{ id: 1, name: 'Office', location_type: 'room', parent_id: null, full_path: 'Office', children: [cabinet] }};
    mockTree = [room];
    stateMod.state.locations = mockTree;
    stateMod.indexLocations();

    // 1. Rapid scan per-row "Box" toggle creates a child box for the toggled row and loose items for the rest
    await modals.openRapidEntryModal(51, {{ manual: true }});
    const jsonPayload = JSON.stringify({{
      items: [
        {{ name: 'Orange Highlighter', estimated_qty: 1 }},
        {{ name: 'Small assorted tools', estimated_qty: 1, brief_specs: 'M2 screwdriver case', _photoPath: '/uploads/originals/m2_crop.jpg' }},
      ],
    }});
    modals.addPartToRapidList(jsonPayload);
    // Toggle row index 1 ('Small assorted tools') to save_as_box
    modals.toggleRapidItemAsBox(1);
    const aiHtml = elements['rapid-ai-results'].innerHTML;
    if (!aiHtml.includes('toggleRapidItemAsBox(1)') || !aiHtml.includes('Saves as Box / Container')) {{
      throw new Error('Expected per-row Box toggle UI to indicate row 1 will save as Box / Container');
    }}
    await modals.submitRapidEntry();

    if (createdLocations.length !== 1 || createdLocations[0].location_type !== 'box' || createdLocations[0].parent_id !== 51) {{
      throw new Error(`Expected 1 box created in Drawer 51, got ${{JSON.stringify(createdLocations)}}`);
    }}
    if (bulkPayloads.length !== 1 || bulkPayloads[0].items.length !== 1 || bulkPayloads[0].items[0].name !== 'Orange Highlighter') {{
      throw new Error(`Expected only Orange Highlighter saved as regular item, got ${{JSON.stringify(bulkPayloads)}}`);
    }}

    // 2. openMoveBoxModal excludes box 88 and descendant 89, and submitMoveBox moves box 88 -> drawer 52
    boxActions.openMoveBoxModal(88);
    const selectHtml = elements['move-box-dest-select'].innerHTML;
    if (selectHtml.includes('value="88"') || selectHtml.includes('value="89"')) {{
      throw new Error('Expected Move Box modal to exclude self and descendants');
    }}
    if (!selectHtml.includes('value="52"')) {{
      throw new Error('Expected Move Box modal to include Drawer 2 (52)');
    }}
    elements['move-box-dest-select'].value = '52';
    await boxActions.submitMoveBox();
    if (patchedLocations.length !== 1 || patchedLocations[0].body.parent_id !== 52) {{
      throw new Error(`Expected PATCH parent_id=52, got ${{JSON.stringify(patchedLocations)}}`);
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"Frontend box conversion behavioral test failed:\n{proc.stderr}"
