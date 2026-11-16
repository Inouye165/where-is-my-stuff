import shutil
import subprocess
from pathlib import Path
import pytest


def test_four_level_hierarchy_rollups_and_search(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Tall 9 drawer storage cabinet", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 7", "location_type": "drawer", "parent_id": cabinet["id"], "grid_row": 3, "grid_col": 1},
    ).json()
    box = client.post(
        "/api/v1/locations",
        json={"name": "Voltage Regulator Assortment", "location_type": "box", "parent_id": drawer["id"]},
    ).json()

    assert box["location_type"] == "box"
    assert box["parent_id"] == drawer["id"]
    assert box["full_path"] == "Office > Tall 9 drawer storage cabinet > Drawer 7 > Voltage Regulator Assortment"
    assert len(box["breadcrumbs"]) == 4

    for code in ("L7824", "L7818", "L7812", "L7805"):
        res = client.post(
            "/api/v1/items",
            json={
                "name": code,
                "category": "Voltage Regulator",
                "quantity": 5,
                "serial_or_part_number": code,
                "location_id": box["id"],
            },
        )
        assert res.status_code == 201, res.text

    # Rollup counts across all 4 levels
    drawer_read = client.get(f"/api/v1/locations/{drawer['id']}").json()
    cabinet_read = client.get(f"/api/v1/locations/{cabinet['id']}").json()
    room_read = client.get(f"/api/v1/locations/{room['id']}").json()
    box_read = client.get(f"/api/v1/locations/{box['id']}").json()

    assert box_read["item_count"] == 4
    assert box_read["total_item_count"] == 4
    assert drawer_read["item_count"] == 0
    assert drawer_read["child_count"] == 1
    assert drawer_read["total_item_count"] == 4
    assert cabinet_read["total_item_count"] == 4
    assert room_read["total_item_count"] == 4

    # Search by part number returns 4-level path & searching by box or drawer name finds descendants
    s_part = client.get("/api/v1/search", params={"q": "L7812"}).json()
    assert s_part["total"] == 1
    assert (
        s_part["items"][0]["location_full_path"]
        == "Office > Tall 9 drawer storage cabinet > Drawer 7 > Voltage Regulator Assortment"
    )

    s_box = client.get("/api/v1/search", params={"q": "Voltage Regulator Assortment"}).json()
    assert s_box["total"] == 4


def test_bulk_create_box_mode_works_in_drawer_and_in_container(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Tall 9 drawer storage cabinet", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 7", "location_type": "drawer", "parent_id": cabinet["id"]},
    ).json()

    # 1. Scan & save_mode='box' inside a Drawer (Level 4 Box)
    res_drawer = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": drawer["id"],
            "save_mode": "box",
            "assortment_name": "Voltage Regulators Kit",
            "assortment_photo": "/uploads/originals/vreg_kit.jpg",
            "category": "Voltage Regulator",
            "items": [
                {"name": "L7824", "quantity": 5, "serial_or_part_number": "L7824", "item_photo": "/uploads/originals/l7824.jpg"},
                {"name": "L7818", "quantity": 5, "serial_or_part_number": "L7818"},
                {"name": "L7815", "quantity": 5, "serial_or_part_number": "L7815"},
            ],
        },
    )
    assert res_drawer.status_code == 201, res_drawer.text
    created = res_drawer.json()
    assert len(created) == 3
    box_id = created[0]["location_id"]
    assert box_id != drawer["id"]
    assert all(it["location_id"] == box_id for it in created)
    assert (
        created[0]["location_full_path"]
        == "Office > Tall 9 drawer storage cabinet > Drawer 7 > Voltage Regulators Kit"
    )

    box_loc = client.get(f"/api/v1/locations/{box_id}").json()
    assert box_loc["location_type"] == "box"
    assert box_loc["parent_id"] == drawer["id"]
    assert box_loc["container_photo"] == "/uploads/originals/vreg_kit.jpg"
    assert len(box_loc["photos"]) == 1

    # 2. Global to containers: save_mode='box' directly inside a Storage Container (e.g. Tote -> Sub-box)
    tote = client.post(
        "/api/v1/locations",
        json={"name": "Clear Electronics Tote", "location_type": "container", "parent_id": room["id"]},
    ).json()
    res_tote = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": tote["id"],
            "save_mode": "box",
            "assortment_name": "Heat Shrink Assortment",
            "raw_lines": "20x 2mm Heat Shrink\n20x 4mm Heat Shrink",
        },
    )
    assert res_tote.status_code == 201, res_tote.text
    tote_items = res_tote.json()
    assert len(tote_items) == 2
    assert tote_items[0]["location_full_path"] == "Office > Clear Electronics Tote > Heat Shrink Assortment"


def test_group_existing_loose_items_into_box_with_audit_trail(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "9 Drawer Cabinet", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer = client.post(
        "/api/v1/locations",
        json={
            "name": "Drawer 7",
            "location_type": "drawer",
            "parent_id": cabinet["id"],
            "container_photo": "/uploads/originals/vreg_box_in_drawer.jpg",
        },
    ).json()

    # Loose items originally added directly to Drawer 7
    loose_ids = []
    for name in ("L7824", "L7818", "L7815", "L7812"):
        it = client.post(
            "/api/v1/items",
            json={"name": name, "quantity": 5, "category": "Voltage Regulator", "location_id": drawer["id"]},
        ).json()
        loose_ids.append(it["id"])
    keep_loose = client.post(
        "/api/v1/items",
        json={"name": "Loose Alligator Clip Lead", "quantity": 2, "location_id": drawer["id"]},
    ).json()

    # Group the 4 voltage regulators into a new Box inside Drawer 7
    grp_res = client.post(
        "/api/v1/items/group-into-box",
        json={
            "parent_location_id": drawer["id"],
            "box_name": "Assorted Voltage Regulators",
            "item_ids": loose_ids,
            "box_photo": "/uploads/originals/vreg_box_in_drawer.jpg",
        },
    )
    assert grp_res.status_code == 201, grp_res.text
    grp_body = grp_res.json()
    assert grp_body["moved_count"] == 4
    new_box = grp_body["box"]
    assert new_box["name"] == "Assorted Voltage Regulators"
    assert new_box["parent_id"] == drawer["id"]
    assert new_box["location_type"] == "box"
    assert new_box["item_count"] == 4

    # Drawer 7 now has 1 loose item + 1 child box (total_item_count = 5)
    drawer_after = client.get(f"/api/v1/locations/{drawer['id']}").json()
    assert drawer_after["item_count"] == 1
    assert drawer_after["child_count"] == 1
    assert drawer_after["total_item_count"] == 5

    # Verify audit log recorded the move on individual items andLoose item stayed in Drawer 7
    moved_detail = client.get(f"/api/v1/items/{loose_ids[0]}").json()
    assert moved_detail["location_id"] == new_box["id"]
    assert any(log["action"] == "moved" for log in moved_detail["audit_logs"])

    kept_detail = client.get(f"/api/v1/items/{keep_loose['id']}").json()
    assert kept_detail["location_id"] == drawer["id"]


def test_frontend_four_level_hierarchy_and_subcontainer_views():
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

    let mockTree = [];
    let mockItems = [];
    const bulkPayloads = [];
    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      if (u.endsWith('/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => ({{ default_provider: 'local', providers: {{}} }}) }};
      }}
      if (u.includes('/locations/tree')) {{
        return {{ ok: true, status: 200, json: async () => mockTree }};
      }}
      if (u.endsWith('/api/v1/items/bulk')) {{
        const body = JSON.parse(opts.body);
        bulkPayloads.push(body);
        return {{ ok: true, status: 201, json: async () => (body.items || []).map((it, idx) => ({{ id: 300 + idx, name: it.name, location_id: 88 }})) }};
      }}
      if (u.includes('/api/v1/items')) {{
        return {{ ok: true, status: 200, json: async () => mockItems }};
      }}
      return {{ ok: true, status: 200, json: async () => [] }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const views = await import('file://{js_dir}/views.js');
    const modals = await import('file://{js_dir}/modals.js');

    // Build 4-level tree: Office (1) -> Cabinet (10) -> Drawer 7 (51) -> Voltage Regulators Box (88)
    const boxNode = {{
      id: 88,
      name: 'Voltage Regulators',
      location_type: 'box',
      parent_id: 51,
      full_path: 'Office > Tall 9 drawer storage cabinet > Drawer 7 > Voltage Regulators',
      item_count: 2,
      total_item_count: 2,
      child_count: 0,
      children: [],
      photos: [{{ id: 21, photo_path: '/uploads/originals/vreg.jpg' }}],
    }};
    const drawerNode = {{
      id: 51,
      name: 'Drawer 7',
      location_type: 'drawer',
      parent_id: 10,
      grid_row: 3,
      grid_col: 1,
      full_path: 'Office > Tall 9 drawer storage cabinet > Drawer 7',
      item_count: 1,
      total_item_count: 3,
      child_count: 1,
      children: [boxNode],
      photos: [],
    }};
    const cabinetNode = {{
      id: 10,
      name: 'Tall 9 drawer storage cabinet',
      location_type: 'container',
      parent_id: 1,
      grid_rows: 9,
      grid_cols: 1,
      full_path: 'Office > Tall 9 drawer storage cabinet',
      item_count: 0,
      total_item_count: 3,
      child_count: 1,
      children: [drawerNode],
      photos: [],
    }};
    const roomNode = {{
      id: 1,
      name: 'Office',
      location_type: 'room',
      parent_id: null,
      full_path: 'Office',
      item_count: 0,
      total_item_count: 3,
      child_count: 1,
      children: [cabinetNode],
      photos: [],
    }};

    mockTree = [roomNode];
    mockItems = [
      {{ id: 501, name: 'L7824', quantity: 5, category: 'Voltage Regulator', location_id: 88 }},
      {{ id: 502, name: 'L7812', quantity: 5, category: 'Voltage Regulator', location_id: 88 }},
      {{ id: 503, name: 'Loose Jumper Wire', quantity: 10, category: 'Wire', location_id: 51 }},
    ];
    stateMod.state.locations = mockTree;
    stateMod.state.items = mockItems;
    stateMod.indexLocations();

    // 1. Drawer 7 must NOT be misclassified into top-level state.containers!
    const containerIds = stateMod.state.containers.map(c => c.id);
    if (containerIds.includes(51) || containerIds.includes(88) || !containerIds.includes(10)) {{
      throw new Error(`Expected only cabinet (10) in state.containers, got ${{JSON.stringify(containerIds)}}`);
    }}

    // 2. getLocationHierarchy(88) preserves room, unit, drawer (with grid coords!), box, and 4-segment humanPath
    const hier = stateMod.getLocationHierarchy(88);
    if (hier.room?.id !== 1 || hier.unit?.id !== 10 || hier.drawer?.id !== 51 || hier.box?.id !== 88) {{
      throw new Error(`Hierarchy mismatch for box 88: room=${{hier.room?.id}}, unit=${{hier.unit?.id}}, drawer=${{hier.drawer?.id}}, box=${{hier.box?.id}}`);
    }}
    if (hier.humanPath !== 'Office › Tall 9 drawer storage cabinet › Drawer 7 › Voltage Regulators') {{
      throw new Error(`Unexpected humanPath: ${{hier.humanPath}}`);
    }}

    // 3. Drawer 7 view renders the child Box card ('Voltage Regulators') AND loose item ('Loose Jumper Wire')
    views.renderDrawerView(51);
    const drawerHtml = elements['app-workspace'].innerHTML;
    if (!drawerHtml.includes('Voltage Regulators') || !drawerHtml.includes('navToDrawer(88')) {{
      throw new Error('Expected Drawer 7 view to render clickable Voltage Regulators box card');
    }}
    if (!drawerHtml.includes('openGroupIntoBoxModal(51)')) {{
      throw new Error('Expected Drawer 7 view to expose Put items in a Box / Kit button when loose items exist');
    }}

    // 4. Box 88 view renders full 4-level breadcrumbs including clickable Drawer 7 parent
    views.renderDrawerView(88);
    const boxHtml = elements['app-workspace'].innerHTML;
    if (!boxHtml.includes('navToDrawer(51') || !boxHtml.includes('L7824')) {{
      throw new Error('Expected Box 88 view to include clickable Drawer 7 breadcrumb and box items');
    }}

    // 5. Scan modal supports save_mode='box' to create a child Box / Kit
    await modals.openRapidEntryModal(51, {{ manual: true }});
    elements['rapid-add-part-input'].value = '5x L7805, 5x L7809';
    modals.addPartToRapidList();
    elements['rapid-assortment-name'].value = 'More Regulators Box';
    modals.setRapidSaveMode('box');
    await modals.submitRapidEntry();

    if (bulkPayloads.length !== 1 || bulkPayloads[0].save_mode !== 'box' || bulkPayloads[0].assortment_name !== 'More Regulators Box') {{
      throw new Error(`Expected bulk POST with save_mode='box', got ${{JSON.stringify(bulkPayloads)}}`);
    }}

    // 6. Drawer quick-add bar has Camera option right next to Add, and child Box is expandable inline!
    views.renderDrawerView(51);
    const dHtmlBefore = elements['app-workspace'].innerHTML;
    if (!dHtmlBefore.includes('drawer-quick-add-bar') || !dHtmlBefore.includes('scanPhoneForLocation(51)')) {{
      throw new Error('Expected Phone Camera / Scan button next to Add in Drawer 7');
    }}
    if (!dHtmlBefore.includes('toggleExpandSubBox(88)')) {{
      throw new Error('Expected Voltage Regulators box in Drawer 7 to be expandable inline');
    }}
    if (!dHtmlBefore.includes('/uploads/originals/vreg.jpg')) {{
      throw new Error('Expected expandable box header to display the main box image');
    }}
    // Toggle expand -> inline contents visible; toggle again -> collapsed
    const initiallyExpanded = dHtmlBefore.includes('subbox-expand-body') && dHtmlBefore.includes('navToItem(501');
    views.toggleExpandSubBox(88);
    const dHtmlAfterToggle = elements['app-workspace'].innerHTML;
    const afterToggleExpanded = dHtmlAfterToggle.includes('subbox-expand-body') && dHtmlAfterToggle.includes('navToItem(501');
    if (initiallyExpanded === afterToggleExpanded) {{
      throw new Error('Expected toggleExpandSubBox(88) to toggle inline visibility of box parts (subbox-expand-body / navToItem(501))');
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"Frontend 4-level test failed:\n{proc.stderr}"


def test_phone_scan_into_box_moves_main_photo_to_box_and_crops_mixed_pixel_bboxes(client):
    from io import BytesIO
    from unittest.mock import AsyncMock, MagicMock, patch
    from PIL import Image, ImageDraw
    from app.services.photo_service import photo_service

    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "Tall 9 drawer storage cabinet", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 7", "location_type": "drawer", "parent_id": cabinet["id"]},
    ).json()

    # Create a 1600x1200 image with two colored compartments:
    # Left compartment (x=250..550, y=350..650 -> RED) has max coord < 1000 in pixels!
    # Right compartment (x=1100..1400, y=350..650 -> GREEN) has max coord > 1000 in pixels!
    img = Image.new("RGB", (1600, 1200), (20, 20, 25))
    draw = ImageDraw.Draw(img)
    draw.rectangle([250, 350, 550, 650], fill=(220, 30, 30))
    draw.rectangle([1100, 350, 1400, 650], fill=(30, 210, 50))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    raw_jpeg = buf.getvalue()

    # 1. Phone capture session uploads photo while user is in Drawer 7
    sess = client.post(
        "/api/v1/photos/capture-sessions",
        json={"label": "Drawer 7", "location_id": drawer["id"]},
    ).json()
    up_res = client.post(
        f"/api/v1/photos/capture-sessions/{sess['token']}/upload",
        files={"file": ("vreg_box.jpg", raw_jpeg, "image/jpeg")},
    )
    assert up_res.status_code == 200, up_res.text
    main_photo_url = up_res.json()["original_path"]

    drawer_mid = client.get(f"/api/v1/locations/{drawer['id']}").json()
    assert len(drawer_mid["photos"]) == 1
    session_photo_id = drawer_mid["photos"][0]["id"]

    # 2. AI Vision (e.g. Qwen2.5-VL) returns PIXEL bboxes where one item <= 1000 and one > 1000
    mock_vision = MagicMock()
    mock_vision.status_code = 200
    mock_vision.json.return_value = {
        "message": {
            "content": (
                '{'
                '"suggested_group_name": "Assorted Voltage Regulators", '
                '"suggested_category": "Voltage Regulator", '
                '"items": ['
                '{"name": "L7815", "part_number": "L7815", "category": "Voltage Regulator", "estimated_qty": 5, "bbox": [270, 370, 530, 630]}, '
                '{"name": "L7824", "part_number": "L7824", "category": "Voltage Regulator", "estimated_qty": 5, "bbox": [1120, 370, 1380, 630]}'
                ']'
                '}'
            )
        }
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_vision)):
        scan_res = client.post(
            f"/api/v1/locations/{drawer['id']}/analyze-photo",
            data={"photo_id": str(session_photo_id), "provider": "local"},
        )
    assert scan_res.status_code == 200, scan_res.text
    scan_body = scan_res.json()
    assert scan_body["suggested_group_name"] == "Assorted Voltage Regulators"
    detected = scan_body["items"]
    assert len(detected) == 2

    # Verify L7815 crop hit the RED region (not shifted into black background by 0-1000 scaling!)
    crop_l7815 = photo_service.resolve_safe_upload_path(detected[0]["crop_photo_path"])
    crop_l7824 = photo_service.resolve_safe_upload_path(detected[1]["crop_photo_path"])
    with Image.open(crop_l7815) as im1:
        r, g, b = im1.getpixel((im1.width // 2, im1.height // 2))
        assert r > 150 and g < 80, f"Expected L7815 crop center to be red, got {(r, g, b)}"
    with Image.open(crop_l7824) as im2:
        r, g, b = im2.getpixel((im2.width // 2, im2.height // 2))
        assert g > 150 and r < 80, f"Expected L7824 crop center to be green, got {(r, g, b)}"

    # 3. Save as Box: main photo moves from Drawer 7 to the new Box, and each part keeps its bbox crop
    bulk_res = client.post(
        "/api/v1/items/bulk",
        json={
            "location_id": drawer["id"],
            "save_mode": "box",
            "assortment_name": scan_body["suggested_group_name"],
            "assortment_photo": main_photo_url,
            "source_photo_ids": [session_photo_id],
            "category": scan_body["suggested_category"],
            "items": [
                {
                    "name": d["name"],
                    "quantity": d["estimated_qty"],
                    "serial_or_part_number": d["part_number"],
                    "item_photo": d["crop_photo_path"],
                }
                for d in detected
            ],
        },
    )
    assert bulk_res.status_code == 201, bulk_res.text
    saved_items = bulk_res.json()
    assert len(saved_items) == 2
    box_id = saved_items[0]["location_id"]

    # Box owns the main photo; Drawer 7 is clean (0 drawer photos); main photo file still exists!
    box_after = client.get(f"/api/v1/locations/{box_id}").json()
    drawer_after = client.get(f"/api/v1/locations/{drawer['id']}").json()
    assert box_after["container_photo"] == main_photo_url
    assert len(box_after["photos"]) == 1
    assert box_after["photos"][0]["photo_path"] == main_photo_url
    assert drawer_after["container_photo"] is None
    assert len(drawer_after["photos"]) == 0
    assert photo_service.resolve_safe_upload_path(main_photo_url).exists()

    # Each part inside the box has its distinct bounding-box crop photo
    assert saved_items[0]["item_photo"] == detected[0]["crop_photo_path"]
    assert saved_items[1]["item_photo"] == detected[1]["crop_photo_path"]
    assert saved_items[0]["item_photo"] != main_photo_url
