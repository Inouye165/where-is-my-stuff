from pathlib import Path
import shutil
import subprocess
import pytest


def test_box_label_lifecycle_rename_and_search_by_label_name_contents(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    cabinet = client.post(
        "/api/v1/locations",
        json={"name": "5 Drawer Cabinet", "location_type": "container", "parent_id": room["id"]},
    ).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 1", "location_type": "drawer", "parent_id": cabinet["id"]},
    ).json()

    # 1. Create a labeled box ("M2" | "Small Assorted Tools")
    box = client.post(
        "/api/v1/locations",
        json={
            "name": "Small Assorted Tools",
            "label": "  M2  ",
            "location_type": "box",
            "parent_id": drawer["id"],
        },
    )
    assert box.status_code == 201, box.text
    box_data = box.json()
    assert box_data["label"] == "M2"
    assert box_data["name"] == "Small Assorted Tools"
    assert box_data["breadcrumbs"][-1]["label"] == "M2"

    # Add an item inside the box
    item = client.post(
        "/api/v1/items",
        json={"name": "T10 Torx Driver", "quantity": 1, "location_id": box_data["id"]},
    ).json()

    # 2. Search by label ("M2"), by box name ("Assorted"), and by contents ("Torx")
    by_label = client.get("/api/v1/search", params={"q": "M2"}).json()
    assert by_label["total"] == 1
    assert by_label["items"][0]["id"] == item["id"]
    assert by_label["items"][0]["location_breadcrumbs"][-1]["label"] == "M2"

    by_name = client.get("/api/v1/search", params={"q": "Assorted"}).json()
    assert by_name["total"] == 1
    assert by_name["items"][0]["id"] == item["id"]

    by_contents = client.get("/api/v1/search", params={"q": "Torx"}).json()
    assert by_contents["total"] == 1
    assert by_contents["items"][0]["id"] == item["id"]

    # 3. Next month, contents/purpose change: rename box name while keeping label "M2"
    renamed = client.patch(
        f"/api/v1/locations/{box_data['id']}",
        json={"name": "Hex Keys & Drivers"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Hex Keys & Drivers"
    assert renamed.json()["label"] == "M2"

    # Still searchable by label "M2" and new name "Hex"
    assert client.get("/api/v1/search", params={"q": "M2"}).json()["total"] == 1
    assert client.get("/api/v1/search", params={"q": "Hex"}).json()["total"] == 1

    # Clearing label with whitespace normalizes to None
    cleared = client.patch(f"/api/v1/locations/{box_data['id']}", json={"label": "   "})
    assert cleared.status_code == 200
    assert cleared.json()["label"] is None


def test_convert_item_to_box_with_label(client):
    room = client.post("/api/v1/locations", json={"name": "Studio", "location_type": "room"}).json()
    drawer = client.post(
        "/api/v1/locations",
        json={"name": "Drawer 1", "location_type": "drawer", "parent_id": room["id"]},
    ).json()
    item = client.post(
        "/api/v1/items",
        json={"name": "Screwdriver", "location_id": drawer["id"]},
    ).json()

    conv = client.post(
        f"/api/v1/items/{item['id']}/convert-to-box",
        json={"box_name": "Precision Tools", "box_label": "M2", "keep_item_inside": True},
    )
    assert conv.status_code == 201, conv.text
    box_data = conv.json()
    assert box_data["name"] == "Precision Tools"
    assert box_data["label"] == "M2"

    # Item inside is now found by searching "M2"
    res = client.get("/api/v1/search", params={"q": "M2"}).json()
    assert res["total"] == 1
    assert res["items"][0]["name"] == "Screwdriver"


def test_frontend_box_label_search_and_convert_behavioral():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    js_dir = (static_dir / "js").as_posix()

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
      'home-search-panel': makeEl('home-search-panel'),
      'convert-to-box-modal': makeEl('convert-to-box-modal'),
      'convert-box-item-id': makeEl('convert-box-item-id'),
      'convert-box-name-input': makeEl('convert-box-name-input'),
      'convert-box-label-input': makeEl('convert-box-label-input'),
      'convert-box-keep-item': makeEl('convert-box-keep-item'),
      'convert-box-preview': makeEl('convert-box-preview'),
      'convert-box-submit-btn': makeEl('convert-box-submit-btn'),
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

    const convertPayloads = [];
    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      if (u.includes('/convert-to-box') && opts.method === 'POST') {{
        const body = JSON.parse(opts.body);
        convertPayloads.push(body);
        return {{ ok: true, status: 201, json: async () => ({{ id: 99, name: body.box_name, label: body.box_label }}) }};
      }}
      if (u.includes('/locations/tree')) {{
        return {{ ok: true, status: 200, json: async () => [] }};
      }}
      if (u.includes('/api/v1/items')) {{
        return {{ ok: true, status: 200, json: async () => [] }};
      }}
      return {{ ok: true, status: 200, json: async () => [] }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const views = await import('file://{js_dir}/views.js');
    const boxActions = await import('file://{js_dir}/box-actions.js');

    const boxNode = {{
      id: 88,
      name: 'Precision Tools',
      label: 'M2',
      location_type: 'box',
      parent_id: 51,
      full_path: 'Office > Cabinet > Drawer 1 > Precision Tools',
      item_count: 0,
      total_item_count: 0,
      child_count: 0,
      children: [],
      photos: [],
    }};
    const drawerNode = {{
      id: 51,
      name: 'Drawer 1',
      location_type: 'drawer',
      parent_id: 10,
      full_path: 'Office > Cabinet > Drawer 1',
      children: [boxNode],
      photos: [],
    }};
    const cabinetNode = {{
      id: 10,
      name: 'Cabinet',
      location_type: 'container',
      parent_id: 1,
      full_path: 'Office > Cabinet',
      children: [drawerNode],
      photos: [],
    }};
    const roomNode = {{
      id: 1,
      name: 'Office',
      location_type: 'room',
      parent_id: null,
      full_path: 'Office',
      children: [cabinetNode],
      photos: [],
    }};

    stateMod.state.locations = [roomNode];
    stateMod.state.items = [{{ id: 501, name: 'Screwdriver', location_id: 51 }}];
    stateMod.indexLocations();

    // 1. Home search matches empty box by label 'M2' and displays the M2 badge
    stateMod.state.homeSearchQuery = 'M2';
    stateMod.state.homeSearchResults = [];
    views.renderHomeSearchPanel();
    const panelHtml = elements['home-search-panel'].innerHTML;
    if (!panelHtml.includes('Precision Tools') || !panelHtml.includes('M2')) {{
      throw new Error(`Expected home search to match box by label M2, got: ${{panelHtml}}`);
    }}

    // 2. Box view renders M2 label badge
    views.renderDrawerView(88);
    const boxHtml = elements['app-workspace'].innerHTML;
    if (!boxHtml.includes('box-label-badge') || !boxHtml.includes('M2')) {{
      throw new Error('Expected box view to render M2 label badge');
    }}

    // 3. Convert Item to Box modal sends box_label
    boxActions.openConvertItemToBoxModal(501);
    elements['convert-box-name-input'].value = 'Assorted Drivers';
    elements['convert-box-label-input'].value = 'M2';
    await boxActions.submitConvertItemToBox();
    if (convertPayloads.length !== 1 || convertPayloads[0].box_label !== 'M2') {{
      throw new Error(`Expected box_label='M2', got ${{JSON.stringify(convertPayloads)}}`);
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"Frontend box label behavioral test failed:\n{proc.stderr}"
