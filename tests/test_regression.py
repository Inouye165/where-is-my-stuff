from app.models.location import Location
from app.models.item import Item

def test_deep_hierarchy_breadcrumbs(client):
    levels = ["Main Ranch", "Outbuilding A", "Woodshop", "Rack 3", "Tier 2", "Bin Red-9"]
    curr_parent = None
    for name in levels:
        resp = client.post("/api/v1/locations", json={
            "name": name,
            "location_type": "container" if "Bin" in name else "room",
            "parent_id": curr_parent
        })
        assert resp.status_code == 201
        curr_parent = resp.json()["id"]

    # Verify deep path
    deep_loc = client.get(f"/api/v1/locations/{curr_parent}").json()
    assert deep_loc["full_path"] == " > ".join(levels)
    assert len(deep_loc["breadcrumbs"]) == 6

def test_unassigned_item_serialization(client):
    # An item without a location shouldn't crash location resolvers
    resp = client.post("/api/v1/items", json={
        "name": "Floating Multimeter",
        "category": "Tools",
        "location_id": None
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["location_name"] is None
    assert data["location_full_path"] is None
    assert data["location_breadcrumbs"] == []
    assert data["location_photos"]["container_photo"] is None
    assert data["location_photos"]["context_photo"] is None

def test_unicode_and_symbols_handling(client):
    loc_resp = client.post("/api/v1/locations", json={
        "name": "Workbench #1 (Electronics & Repair) - 1/4\" Area",
        "location_type": "room"
    })
    assert loc_resp.status_code == 201

    item_resp = client.post("/api/v1/items", json={
        "name": "10kΩ 1/4W Resistors & 2.5µF Capacitors",
        "tags": "resistor,10kΩ,microfarad,µF",
        "location_id": loc_resp.json()["id"]
    })
    assert item_resp.status_code == 201
    assert "10kΩ" in item_resp.json()["name"]

    # Search with unicode token
    search_resp = client.get("/api/v1/search?q=10kΩ").json()
    assert search_resp["total"] >= 1
    assert search_resp["items"][0]["name"] == "10kΩ 1/4W Resistors & 2.5µF Capacitors"

def test_delete_location_cascades_sublocations_cleanly(client):
    p = client.post("/api/v1/locations", json={"name": "Temp Parent"}).json()
    c = client.post("/api/v1/locations", json={"name": "Temp Child", "parent_id": p["id"]}).json()

    del_resp = client.delete(f"/api/v1/locations/{p['id']}")
    assert del_resp.status_code == 204

    # Child should also be gone
    get_child = client.get(f"/api/v1/locations/{c['id']}")
    assert get_child.status_code == 404

def test_item_workbench_status_and_audit(client):
    loc = client.post("/api/v1/locations", json={"name": "Electronics Bin #1"}).json()
    item = client.post("/api/v1/items", json={
        "name": "Soldering Iron",
        "location_id": loc["id"],
        "status": "stored"
    }).json()
    assert item["status"] == "stored"

    # Checkout to workbench
    update_resp = client.put(f"/api/v1/items/{item['id']}", json={
        "status": "in_use",
        "status_note": "Soldering audio harness"
    })
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["status"] == "in_use"
    assert updated["status_note"] == "Soldering audio harness"

    # Filter by status
    in_use_items = client.get("/api/v1/items?status=in_use").json()
    assert any(i["id"] == item["id"] for i in in_use_items)

    stored_items = client.get("/api/v1/items?status=stored").json()
    assert not any(i["id"] == item["id"] for i in stored_items)

def test_items_bulk_creation(client):
    loc = client.post("/api/v1/locations", json={"name": "Hardware Drawer 4"}).json()
    bulk_payload = {
        "location_id": loc["id"],
        "category": "Hardware & Fasteners",
        "raw_text": "4x M3 Brass Standoffs\nNeedle Nose Pliers\nBlue Electrical Tape (2 rolls)\n10 pcs 100k Resistors"
    }
    resp = client.post("/api/v1/items/bulk", json=bulk_payload)
    assert resp.status_code == 201
    created_items = resp.json()
    assert len(created_items) == 4

    names = {i["name"]: i["quantity"] for i in created_items}
    assert names["M3 Brass Standoffs"] == 4
    assert names["Needle Nose Pliers"] == 1
    assert names["Blue Electrical Tape"] == 2
    assert names["100k Resistors"] == 10

def test_location_qr_generation(client):
    loc = client.post("/api/v1/locations", json={"name": "Yellow Organizer B1"}).json()
    qr_resp = client.get(f"/api/v1/locations/{loc['id']}/qr")
    assert qr_resp.status_code == 200
    assert qr_resp.headers["content-type"] == "image/png"
    assert len(qr_resp.content) > 100

def test_location_photo_analysis_fallback(client):
    loc = client.post("/api/v1/locations", json={"name": "Component Drawer"}).json()
    resp = client.post(f"/api/v1/locations/{loc['id']}/analyze-photo", json={"photo_url": "/api/v1/photos/test.jpg"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("fallback", "success")
    assert "instructions" in data

def test_frontend_js_syntax():
    import subprocess
    import shutil
    from pathlib import Path
    import pytest

    js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "app.js"
    assert js_path.exists(), f"Frontend script not found: {js_path}"

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js is not installed or not in PATH")

    proc = subprocess.run([node_exe, "--check", str(js_path)], capture_output=True, text=True)
    assert proc.returncode == 0, f"JavaScript syntax check failed:\n{proc.stderr}"

def test_html_inline_handlers_exist_in_js():
    import re
    from pathlib import Path

    html_path = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"
    js_path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "app.js"
    assert html_path.exists()
    assert js_path.exists()

    html = html_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")

    handlers = set(re.findall(r'on\w+=\s*["\x27](?:return\s+)?([a-zA-Z0-9_]+)\s*\(', html))
    assert len(handlers) > 0, "No inline handlers found in index.html to validate"

    missing = []
    for h in handlers:
        pattern = rf'(function\s+{h}\b|window\.{h}\b|{h}\s*:\s*{h}\b|const\s+{h}\s*=|let\s+{h}\s*=|var\s+{h}\s*=)'
        if not re.search(pattern, js):
            missing.append(h)

    assert not missing, f"Inline handlers in index.html missing in app.js: {missing}"

def test_qr_lan_ip_resolution_and_override(client, monkeypatch):
    from app.core.network import get_lan_ip, resolve_effective_base_url

    lan_ip = get_lan_ip()
    assert lan_ip != ""

    # Test resolution when request host is localhost
    resolved = resolve_effective_base_url(host_header="localhost:8000")
    assert "localhost" not in resolved
    assert lan_ip in resolved

    # Test explicit base_url param
    custom = resolve_effective_base_url(base_url_param="http://workshop-pi:8000")
    assert custom == "http://workshop-pi:8000"

    # Test network-info endpoint
    net_resp = client.get("/api/v1/locations/network-info")
    assert net_resp.status_code == 200
    net_data = net_resp.json()
    assert "lan_ip" in net_data
    assert "base_url" in net_data

    # Test QR endpoint with custom base_url
    loc = client.post("/api/v1/locations", json={"name": "Network Bin"}).json()
    qr_custom = client.get(f"/api/v1/locations/{loc['id']}/qr?base_url=http://192.168.1.150:8000")
    assert qr_custom.status_code == 200
    assert qr_custom.headers["content-type"] == "image/png"

def test_photo_service_dimension_capping_and_orientation(client):
    import io
    from PIL import Image

    # Create large image 2400x1800 with RGBA mode
    img = Image.new("RGBA", (2400, 1800), color=(255, 128, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    files = {"file": ("huge_phone_capture.png", buf, "image/png")}
    resp = client.post("/api/v1/photos/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()

    # Verify saved image dimensions are capped at 1600
    from app.core.config import settings
    saved_path = settings.ORIGINALS_DIR / data["filename"]
    assert saved_path.exists()

    with Image.open(saved_path) as saved_img:
        assert max(saved_img.width, saved_img.height) <= 1600


def test_location_put_and_bulk_item_default_category(client):
    room = client.post("/api/v1/locations", json={"name": "Garage", "location_type": "room"}).json()
    container = client.post("/api/v1/locations", json={"name": "Red Tote", "location_type": "container"}).json()

    # PUT should update parent_id cleanly alongside PATCH
    put_resp = client.put(f"/api/v1/locations/{container['id']}", json={"parent_id": room["id"]})
    assert put_resp.status_code == 200
    assert put_resp.json()["parent_id"] == room["id"]

    # Bulk item creation should apply default category when provided
    bulk_resp = client.post("/api/v1/items/bulk", json={
        "location_id": container["id"],
        "category": "Hand Tools",
        "raw_lines": "2x Scissors\nMasking Tape"
    })
    assert bulk_resp.status_code == 201
    created = bulk_resp.json()
    assert len(created) == 2
    assert all(it["category"] == "Hand Tools" for it in created)


def test_consumer_ux_navigation_and_jargon_free_markup():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "css" / "app.css").read_text(encoding="utf-8")
    js = (root / "js" / "app.js").read_text(encoding="utf-8")

    # 1. Unified '+' sheet & Navigation slots
    assert "Add to Inventory" in html
    assert "Scan with Camera" in html
    assert "Add Item Manually" in html
    assert "Add Location or Container" in html
    for slot in ("Places", "Items", "More"):
        assert slot in html, f"Missing navigation slot '{slot}' in index.html"
    assert "In Use" not in html
    assert "Out right now" not in html

    # 2. Zero-scroll 100dvh shell + responsive 3x2 / 2x3 room grid + Needs a home
    assert "100dvh" in css
    assert "What are you looking for?" in html or "What are you looking for?" in js
    assert "Needs a home" in js
    assert "prefers-color-scheme" in js

    # 3. Scrubbed system/warehouse jargon from static HTML
    forbidden_html_phrases = [
        "+ Add Storage",
        "Storage Units",
        "Snap Cover",
        "Bin Exterior",
        "Locate in Unit",
        "Pull to Workbench",
        "QTY: UNKNOWN",
    ]
    for phrase in forbidden_html_phrases:
        assert phrase not in html, f"Deprecated jargon '{phrase}' still present in index.html"

    # 4. Every inline handler in index.html must be explicitly exposed in Object.assign(window, {...})
    import re
    window_block_match = re.search(r"Object\.assign\(\s*window\s*,\s*\{([^}]+)\}", js, re.DOTALL)
    assert window_block_match, "Expected Object.assign(window, {...}) export block in app.js"
    exported_names = set(re.findall(r"\b([a-zA-Z0-9_]+)\b", window_block_match.group(1)))
    html_handlers = set(re.findall(r'on\w+=\s*["\x27](?:return\s+)?([a-zA-Z0-9_]+)\s*\(', html))
    missing_window = html_handlers - exported_names
    assert not missing_window, f"Inline handlers in index.html not exposed on window: {missing_window}"


def test_landing_page_v2_ux_invariants():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "css" / "app.css").read_text(encoding="utf-8")
    views_js = (root / "js" / "views.js").read_text(encoding="utf-8")
    router_js = (root / "js" / "router.js").read_text(encoding="utf-8")
    app_js = (root / "js" / "app.js").read_text(encoding="utf-8")
    api_js = (root / "js" / "api.js").read_text(encoding="utf-8")

    # 1. Consistent Lucide line icons, Items tab does NOT use magnifier
    assert "lucide" in views_js.lower() or "stroke-width" in views_js
    assert 'id="rail-nav-items"' in html
    items_btn_idx = html.index('id="rail-nav-items"')
    items_btn_snippet = html[items_btn_idx:items_btn_idx + 350]
    assert 'cx="11" cy="11"' not in items_btn_snippet, "Items tab must use a list or box icon, not a magnifier"

    # 2. Empty look & tile content
    assert "Nothing here yet" in views_js
    assert "Where does this live?" in views_js
    assert "Add a photo" in views_js
    assert "Empty — tap to add" not in views_js
    assert "Empty - tap to add" not in views_js
    assert "Out right now" not in views_js
    assert "renderInUseView" not in views_js

    # 3. Wide screen right column & reduced motion & cross-tab sync & dynamic title
    assert "Recently added" in views_js
    assert "min-width: 1401px" in css or "min-width: 1400px" in css
    assert "prefers-reduced-motion" in css
    assert "BroadcastChannel" in app_js or "BroadcastChannel" in api_js
    assert "document.title" in router_js


def test_delete_room_preserves_containers_and_items_in_needs_home(client):
    room = client.post("/api/v1/locations", json={"name": "Spare Room", "location_type": "room"}).json()
    box = client.post("/api/v1/locations", json={"name": "Winter Box #1", "location_type": "container", "parent_id": room["id"]}).json()
    drawer = client.post("/api/v1/locations", json={"name": "Top Tray", "location_type": "bin", "parent_id": box["id"]}).json()
    boxed_item = client.post("/api/v1/items", json={"name": "Scarf", "location_id": drawer["id"]}).json()
    loose_item = client.post("/api/v1/items", json={"name": "Floor Lamp", "location_id": room["id"]}).json()

    del_resp = client.delete(f"/api/v1/locations/{room['id']}")
    assert del_resp.status_code == 204

    # Room is gone
    assert client.get(f"/api/v1/locations/{room['id']}").status_code == 404

    # Labeled box and its drawer + item are preserved in Needs a home (parent_id=None)
    box_after = client.get(f"/api/v1/locations/{box['id']}")
    assert box_after.status_code == 200
    assert box_after.json()["parent_id"] is None

    boxed_after = client.get(f"/api/v1/items/{boxed_item['id']}")
    assert boxed_after.status_code == 200
    assert boxed_after.json()["location_id"] == drawer["id"]

    # Loose item is preserved with location_id=None (Needs a home)
    loose_after = client.get(f"/api/v1/items/{loose_item['id']}")
    assert loose_after.status_code == 200
    assert loose_after.json()["location_id"] is None


def test_move_room_into_another_room(client):
    bedroom = client.post("/api/v1/locations", json={"name": "Bedroom", "location_type": "room"}).json()
    closet = client.post("/api/v1/locations", json={"name": "Closet", "location_type": "room"}).json()

    moved = client.patch(f"/api/v1/locations/{closet['id']}", json={"parent_id": bedroom["id"]})
    assert moved.status_code == 200
    data = moved.json()
    assert data["parent_id"] == bedroom["id"]
    assert data["full_path"] == "Bedroom > Closet"









