import shutil
import subprocess
from pathlib import Path

import pytest


def test_attach_and_detach_loose_container_and_box_api(client):
    room = client.post(
        "/api/v1/locations",
        json={"name": "Office", "location_type": "room"},
    ).json()

    loose_container = client.post(
        "/api/v1/locations",
        json={"name": "Rolling Tool Cart", "location_type": "container", "parent_id": None},
    ).json()
    loose_box = client.post(
        "/api/v1/locations",
        json={"name": "Hardware Tote", "label": "M2", "location_type": "box", "parent_id": None},
    ).json()

    assert loose_container["parent_id"] is None
    assert loose_box["parent_id"] is None
    assert loose_box["label"] == "M2"

    # Attach both to Office
    attached_c = client.patch(
        f"/api/v1/locations/{loose_container['id']}",
        json={"parent_id": room["id"]},
    )
    assert attached_c.status_code == 200, attached_c.text
    assert attached_c.json()["parent_id"] == room["id"]
    assert "Office" in attached_c.json()["full_path"]

    attached_b = client.patch(
        f"/api/v1/locations/{loose_box['id']}",
        json={"parent_id": room["id"]},
    )
    assert attached_b.status_code == 200, attached_b.text
    assert attached_b.json()["parent_id"] == room["id"]

    # Detach both back to loose (parent_id = None)
    detached_c = client.patch(
        f"/api/v1/locations/{loose_container['id']}",
        json={"parent_id": None},
    )
    assert detached_c.status_code == 200, detached_c.text
    assert detached_c.json()["parent_id"] is None
    assert detached_c.json()["full_path"] == "Rolling Tool Cart"

    detached_b = client.patch(
        f"/api/v1/locations/{loose_box['id']}",
        json={"parent_id": None},
    )
    assert detached_b.status_code == 200, detached_b.text
    assert detached_b.json()["parent_id"] is None
    assert detached_b.json()["full_path"] == "Hardware Tote"


def test_loose_boxes_view_includes_standalone_boxes_and_supports_detach_behavioral():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.BroadcastChannel = undefined;

    function makeEl(id) {{
      const classes = new Set();
      return {{
        id,
        value: '',
        textContent: '',
        innerHTML: '',
        style: {{ display: '' }},
        dataset: {{}},
        scrollTop: 0,
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
        setAttribute() {{}},
        getAttribute() {{ return null; }},
        appendChild() {{}},
        addEventListener() {{}},
        querySelector() {{ return null; }},
      }};
    }}

    const workspace = makeEl('app-workspace');
    const stageBody = makeEl('home-stage-body');
    const elements = {{
      'app-workspace': workspace,
      'home-stage-body': stageBody,
      'toast-shelf': makeEl('toast-shelf'),
    }};

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

    let patchCalls = [];
    globalThis.fetch = async (url, init = {{}}) => {{
      const u = String(url);
      if (init.method === 'PATCH' && u.includes('/api/v1/locations/')) {{
        patchCalls.push({{ url: u, body: JSON.parse(init.body) }});
        return {{ ok: true, status: 200, json: async () => ({{ id: 12, parent_id: null }}) }};
      }}
      if (u.includes('/api/v1/locations') || u.includes('/api/v1/items')) {{
        return {{ ok: true, status: 200, json: async () => [] }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const viewsMod = await import('file://{js_dir}/views.js');

    stateMod.state.locations = [
      {{
        id: 1,
        name: 'Office',
        location_type: 'room',
        parent_id: null,
        full_path: 'Office',
        children: [
          {{
            id: 12,
            name: 'Attached Shelf',
            location_type: 'container',
            parent_id: 1,
            full_path: 'Office > Attached Shelf',
            children: [],
          }},
        ],
      }},
      {{
        id: 2,
        name: 'Loose Cabinet',
        location_type: 'container',
        parent_id: null,
        full_path: 'Loose Cabinet',
        children: [],
      }},
      {{
        id: 3,
        name: 'Hardware Tote',
        label: 'M2',
        location_type: 'box',
        parent_id: null,
        full_path: 'Hardware Tote',
        children: [],
      }},
    ];
    stateMod.state.items = [];
    stateMod.indexLocations();

    // 1. Standalone loose box (location_type='box', children=[]) MUST be included in unassigned containers
    const loose = stateMod.getUnassignedContainers();
    const looseIds = loose.map(c => c.id);
    if (!looseIds.includes(2) || !looseIds.includes(3)) {{
      throw new Error(`Expected loose containers [2, 3], got ${{JSON.stringify(looseIds)}}`);
    }}

    // 2. Home stage header has both Loose boxes button and + New container button
    viewsMod.renderHomeStageBody();
    if (!stageBody.innerHTML.includes('New container')) {{
      throw new Error('Expected Home header to include New container button');
    }}
    if (!stageBody.innerHTML.includes('Loose boxes')) {{
      throw new Error('Expected Home header to include Loose boxes button');
    }}

    // 3. Needs a home view lists ALL loose boxes & containers at once in a grid
    viewsMod.renderNeedsHomeView();
    if (!workspace.innerHTML.includes('loose-containers-grid')) {{
      throw new Error('Expected renderNeedsHomeView to render loose-containers-grid');
    }}
    if (!workspace.innerHTML.includes('Loose Cabinet') || !workspace.innerHTML.includes('Hardware Tote')) {{
      throw new Error('Expected both Loose Cabinet and Hardware Tote to be visible simultaneously');
    }}
    if (!workspace.innerHTML.includes('M2')) {{
      throw new Error('Expected box label M2 to be rendered on loose box card');
    }}

    // 4. detachLocationFromSpace sends PATCH parent_id: null
    if (typeof viewsMod.detachLocationFromSpace !== 'function') {{
      throw new Error('detachLocationFromSpace is not exported from views.js');
    }}
    await viewsMod.detachLocationFromSpace(12);
    if (patchCalls.length !== 1 || patchCalls[0].body.parent_id !== null) {{
      throw new Error(`Expected PATCH parent_id: null, got ${{JSON.stringify(patchCalls)}}`);
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Loose boxes & detach behavioral test failed:\n{proc.stderr}"
