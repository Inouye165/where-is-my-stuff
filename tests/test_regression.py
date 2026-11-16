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
    for slot in ("Spaces", "Items", "More"):
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


def test_item_attachment_image_preserved_when_item_photo_replaced(client):
    import io
    from PIL import Image
    from app.core.config import settings

    def make_jpeg(color):
        buf = io.BytesIO()
        Image.new("RGB", (80, 80), color=color).save(buf, format="JPEG")
        buf.seek(0)
        return buf

    item = client.post("/api/v1/items", json={"name": "Oscilloscope Probe"}).json()

    # Uploading an image attachment auto-populates item_photo with att.file_path
    att_res = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("probe_chart.jpg", make_jpeg((20, 80, 160)), "image/jpeg")},
        data={"title": "Compensation Chart"},
    )
    assert att_res.status_code == 201
    att = att_res.json()
    att_filename = att["file_path"].split("/")[-1]
    att_disk_path = settings.ORIGINALS_DIR / att_filename
    assert att_disk_path.exists()

    # Replace item_photo with a different uploaded image
    new_photo = client.post(
        "/api/v1/photos/upload",
        files={"file": ("probe_main.jpg", make_jpeg((180, 60, 40)), "image/jpeg")},
    ).json()
    upd = client.patch(f"/api/v1/items/{item['id']}", json={"item_photo": new_photo["original_path"]})
    assert upd.status_code == 200

    # Attachment still references att_disk_path, so it MUST NOT be deleted from disk
    assert att_disk_path.exists(), "Attachment image file was deleted while ItemAttachment still referenced it"


def test_rollback_preserves_files_and_cascade_deletes_clean_orphans(client, db_session):
    import io
    from PIL import Image
    from app.core.config import settings
    from app.schemas.item import ItemUpdate
    from app.services.inventory_service import inventory_service

    def make_jpeg(color):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color=color).save(buf, format="JPEG")
        buf.seek(0)
        return buf

    # 1. Pre-commit rollback safety: replacing item_photo then rolling back must NOT unlink old file
    p_orig = client.post("/api/v1/photos/upload", files={"file": ("orig.jpg", make_jpeg((10, 20, 30)), "image/jpeg")}).json()
    p_repl = client.post("/api/v1/photos/upload", files={"file": ("repl.jpg", make_jpeg((40, 50, 60)), "image/jpeg")}).json()
    orig_file = settings.ORIGINALS_DIR / p_orig["filename"]
    assert orig_file.exists()

    item = client.post("/api/v1/items", json={"name": "Bench Supply", "item_photo": p_orig["original_path"]}).json()

    inventory_service.update(db_session, item["id"], ItemUpdate(item_photo=p_repl["original_path"]))
    assert orig_file.exists(), "Old photo unlinked before transaction committed"
    db_session.rollback()
    assert orig_file.exists(), "Old photo missing after transaction rollback"

    # 2. Item delete cleans up attached PDFs and extra gallery image attachments
    pdf_att = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("manual.pdf", b"%PDF-1.4\n%manual\n", "application/pdf")},
    ).json()
    extra_img_att = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("extra.jpg", make_jpeg((70, 80, 90)), "image/jpeg")},
    ).json()
    pdf_disk = settings.ATTACHMENTS_DIR / pdf_att["file_path"].split("/")[-1]
    extra_img_disk = settings.ORIGINALS_DIR / extra_img_att["file_path"].split("/")[-1]
    assert pdf_disk.exists() and extra_img_disk.exists()

    assert client.delete(f"/api/v1/items/{item['id']}").status_code == 204
    assert not pdf_disk.exists(), "PDF attachment leaked on item delete"
    assert not extra_img_disk.exists(), "Image attachment leaked on item delete"
    assert not orig_file.exists(), "Item photo leaked on item delete"

    # 3. Location delete & grid drawer removal clean up LocationPhoto gallery files
    cabinet = client.post("/api/v1/locations", json={"name": "Parts Cabinet", "location_type": "container"}).json()
    d1 = client.post("/api/v1/locations", json={"name": "Drawer 1", "location_type": "drawer", "parent_id": cabinet["id"]}).json()
    d2 = client.post("/api/v1/locations", json={"name": "Drawer 2", "location_type": "drawer", "parent_id": cabinet["id"]}).json()

    d1_gal1 = client.post(
        f"/api/v1/locations/{d1['id']}/photos",
        files={"file": ("d1_a.jpg", make_jpeg((11, 22, 33)), "image/jpeg")},
    ).json()
    d1_gal2 = client.post(
        f"/api/v1/locations/{d1['id']}/photos",
        files={"file": ("d1_b.jpg", make_jpeg((44, 55, 66)), "image/jpeg")},
    ).json()
    d2_gal1 = client.post(
        f"/api/v1/locations/{d2['id']}/photos",
        files={"file": ("d2_a.jpg", make_jpeg((77, 88, 99)), "image/jpeg")},
    ).json()

    d1_gal2_disk = settings.ORIGINALS_DIR / d1_gal2["photo_path"].split("/")[-1]
    d2_gal1_disk = settings.ORIGINALS_DIR / d2_gal1["photo_path"].split("/")[-1]
    assert d1_gal2_disk.exists() and d2_gal1_disk.exists()

    # Grid save keeping only d1 removes d2 and cleans d2's gallery files
    grid_res = client.put(
        f"/api/v1/locations/{cabinet['id']}/grid",
        json={
            "grid_rows": 1,
            "grid_cols": 1,
            "slots": [
                {
                    "id": d1["id"],
                    "name": "Drawer 1",
                    "location_type": "drawer",
                    "grid_row": 1,
                    "grid_col": 1,
                    "grid_row_span": 1,
                    "grid_col_span": 1,
                }
            ],
        },
    )
    assert grid_res.status_code == 200
    assert not d2_gal1_disk.exists(), "Removed grid drawer gallery photo leaked on disk"

    # Deleting cabinet cascades d1 and cleans all d1 gallery photos (not just container_photo)
    assert client.delete(f"/api/v1/locations/{cabinet['id']}").status_code == 204
    assert not (settings.ORIGINALS_DIR / d1_gal1["photo_path"].split("/")[-1]).exists()
    assert not d1_gal2_disk.exists(), "Secondary LocationPhoto gallery image leaked on location delete"


def test_capture_session_snapshot_isolation_and_expired_upload_cleanup(client, monkeypatch):
    import io
    from datetime import datetime, timedelta, timezone
    from PIL import Image
    from app.core.config import settings
    from app.services.capture_session_service import capture_session_service

    sess_data = client.post("/api/v1/photos/capture-sessions", json={"label": "Test Bin"}).json()
    token = sess_data["token"]

    # Snapshot returned by get_session must not expose internal mutable photos list
    snap = capture_session_service.get_session(token)
    snap.photos.append({"filename": "mutated.jpg"})
    assert capture_session_service.get_session(token).photos == []

    # If session expires while photo_service.process_and_save is running, 410 must not leave orphan files
    before_originals = set(settings.ORIGINALS_DIR.glob("*"))
    before_thumbs = set(settings.THUMBNAILS_DIR.glob("*"))

    orig_process = capture_session_service.mark_uploaded

    def expire_then_mark(tok: str, photo_result: dict):
        with capture_session_service._lock:
            capture_session_service._sessions[tok].expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        return orig_process(tok, photo_result)

    monkeypatch.setattr(capture_session_service, "mark_uploaded", expire_then_mark)

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(120, 30, 60)).save(buf, format="JPEG")
    buf.seek(0)
    res = client.post(
        f"/api/v1/photos/capture-sessions/{token}/upload",
        files={"file": ("late_phone.jpg", buf, "image/jpeg")},
    )
    assert res.status_code == 410
    assert set(settings.ORIGINALS_DIR.glob("*")) == before_originals
    assert set(settings.THUMBNAILS_DIR.glob("*")) == before_thumbs


def test_sync_photo_routes_use_threadpool():
    import inspect
    from app.api import photos

    assert not inspect.iscoroutinefunction(photos.upload_photo)
    assert not inspect.iscoroutinefunction(photos.get_capture_session_qr)
    assert not inspect.iscoroutinefunction(photos.upload_capture_session_photo)


def test_frontend_concurrency_offline_shell_and_runtime_guards():
    import re
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    js_dir = static_dir / "js"

    # 1. No undeclared rapidSelectedFile assignment in modals.js
    modals_src = (js_dir / "modals.js").read_text(encoding="utf-8")
    assert "rapidSelectedFile " not in modals_src and "rapidSelectedFile=" not in modals_src

    # 2. Every /js/*.js module imported by static JS files is cached in sw.js APP_SHELL_URLS
    sw_src = (static_dir / "sw.js").read_text(encoding="utf-8")
    for js_file in js_dir.glob("*.js"):
        src = js_file.read_text(encoding="utf-8")
        for rel_import in re.findall(r"from\s+['\"]\.\/([a-zA-Z0-9_-]+\.js)['\"]", src):
            expected_url = f"/js/{rel_import}"
            assert expected_url in sw_src, f"{expected_url} imported by {js_file.name} missing from sw.js APP_SHELL_URLS"

    # 3. Every literal icon requested via lucideIcon('name') exists in icons.js
    icons_src = (js_dir / "icons.js").read_text(encoding="utf-8")
    defined_icons = set(re.findall(r"^\s*([a-zA-Z0-9_]+)\s*:\s*'", icons_src, re.MULTILINE))
    for js_file in js_dir.glob("*.js"):
        src = js_file.read_text(encoding="utf-8")
        for icon_name in re.findall(r"lucideIcon\(\s*['\"]([a-zA-Z0-9_]+)['\"]", src):
            assert icon_name in defined_icons, f"Icon '{icon_name}' used in {js_file.name} missing from icons.js"

    # 4. Behavioral Node ESM test for unsaved-guard promise resolution & single cross-tab sync firing
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    unsaved_url = (js_dir / "unsaved-guard.js").as_uri()
    api_url = (js_dir / "api.js").as_uri()
    script = f"""
    const listeners = {{}};
    let bcHandler = null;
    globalThis.window = {{
      addEventListener(ev, fn) {{ (listeners[ev] ||= []).push(fn); }},
      confirm: () => true,
    }};
    globalThis.localStorage = {{
      setItem(k, v) {{
        for (const fn of (listeners['storage'] || [])) fn({{ key: k, newValue: v }});
      }},
    }};
    globalThis.BroadcastChannel = class {{
      addEventListener(ev, fn) {{ if (ev === 'message') bcHandler = fn; }}
      postMessage(data) {{ if (bcHandler) bcHandler({{ data }}); }}
    }};
    const modalEl = {{
      classList: {{ add() {{}}, remove() {{}}, contains: () => true }},
      dataset: {{}},
    }};
    globalThis.document = {{
      getElementById(id) {{
        if (id === 'unsaved-confirm-modal') return modalEl;
        if (id === 'unsaved-confirm-msg') return {{ textContent: '' }};
        return null;
      }},
    }};

    const guard = await import({unsaved_url!r});
    const p1 = guard.showUnsavedPrompt('First');
    const p2 = guard.showUnsavedPrompt('Second');
    guard.resolveUnsavedPrompt('discard');
    const [r1, r2] = await Promise.race([
      Promise.all([p1, p2]),
      new Promise((_, rej) => setTimeout(() => rej(new Error('First unsaved prompt hung forever')), 250)),
    ]);
    if (r1 !== 'cancel' || r2 !== 'discard') {{
      throw new Error('Unexpected prompt resolutions: ' + r1 + ', ' + r2);
    }}

    const api = await import({api_url!r});
    let syncCount = 0;
    api.onCrossTabSync(() => {{ syncCount += 1; }});
    api.notifyCrossTabSync();
    await new Promise(r => setTimeout(r, 120));
    if (syncCount !== 1) {{
      throw new Error('Expected exactly 1 cross-tab sync callback, got ' + syncCount);
    }}
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Frontend concurrency test failed:\n{proc.stderr}"


def test_no_duplicate_containers_router_and_location_photo_sync(client):
    import io
    from PIL import Image
    from app.core.config import settings

    # Duplicate /api/v1/containers/locations mount must be gone
    dup_res = client.get("/api/v1/containers/locations")
    assert dup_res.status_code == 404

    def make_jpeg(color):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color=color).save(buf, format="JPEG")
        buf.seek(0)
        return buf

    unit = client.post("/api/v1/locations", json={"name": "Sync Unit", "location_type": "container"}).json()
    p1 = client.post(
        f"/api/v1/locations/{unit['id']}/photos",
        files={"file": ("unit1.jpg", make_jpeg((30, 90, 150)), "image/jpeg")},
    ).json()
    p1_disk = settings.ORIGINALS_DIR / p1["photo_path"].split("/")[-1]
    assert p1_disk.exists()

    # Clearing container_photo via PATCH (unitViewerRemovePhoto) also clears container LocationPhoto rows & files
    clear_res = client.patch(
        f"/api/v1/locations/{unit['id']}",
        json={"container_photo": None, "context_photo": None},
    )
    assert clear_res.status_code == 200
    assert clear_res.json()["photos"] == []
    assert not p1_disk.exists()

    # Setting container_photo via PATCH (Quick Photo / Crop Editor) syncs a LocationPhoto row
    up2 = client.post(
        "/api/v1/photos/upload",
        files={"file": ("unit2.jpg", make_jpeg((150, 90, 30)), "image/jpeg")},
    ).json()
    set_res = client.patch(
        f"/api/v1/locations/{unit['id']}",
        json={"container_photo": up2["original_path"]},
    )
    assert set_res.status_code == 200
    assert any(p["photo_path"] == up2["original_path"] for p in set_res.json()["photos"])


def test_photo_search_delegates_to_vision_service_and_snapshot_skips_unused_queries(client, db_session):
    import io
    from unittest.mock import AsyncMock, patch
    from PIL import Image
    from sqlalchemy import event
    from app.services.inventory_service import inventory_service
    from app.services.vision_service import vision_service
    from app.repositories.item_repository import item_repository

    loc = client.post("/api/v1/locations", json={"name": "Bench", "location_type": "room"}).json()
    client.post("/api/v1/items", json={"name": "Digital Caliper", "location_id": loc["id"]})

    # 1. Photo search uses vision_service.extract_search_query
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color=(80, 80, 80)).save(buf, format="JPEG")
    buf.seek(0)
    with patch.object(
        vision_service,
        "extract_search_query",
        new=AsyncMock(return_value=("Digital Caliper", True)),
    ) as mock_extract:
        res = client.post("/api/v1/search/photo", files={"file": ("snap.jpg", buf, "image/jpeg")})
    assert res.status_code == 200
    assert mock_extract.await_count == 1
    assert res.json()["query"] == "Digital Caliper"
    assert len(res.json()["items"]) == 1

    # 2. Item list serialization does not query location_photos unnecessarily
    statements = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    items = item_repository.list_recent(db_session, limit=10)
    event.listen(db_session.get_bind(), "before_cursor_execute", capture_sql)
    try:
        inventory_service.serialize_many(db_session, items)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture_sql)

    assert not any("location_photos" in s for s in statements), f"Unexpected location_photos query: {statements}"


def test_dead_code_and_redundancies_eliminated():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    static_dir = root / "app" / "static"
    js_dir = static_dir / "js"

    html = (static_dir / "index.html").read_text(encoding="utf-8")
    capture_html = (static_dir / "capture.html").read_text(encoding="utf-8")
    views_js = (js_dir / "views.js").read_text(encoding="utf-8")
    modals_js = (js_dir / "modals.js").read_text(encoding="utf-8")
    grid_js = (js_dir / "grid-designer.js").read_text(encoding="utf-8")

    # Dead modals/drawers/buttons removed from HTML
    assert 'id="grid-designer-modal"' not in html
    assert 'id="item-slide-drawer"' not in html
    assert 'id="retake-btn"' not in capture_html

    # No duplicate static IDs in index.html, and no collision with inline grid designer IDs
    html_ids = re.findall(r'\bid="([^"$]+)"', html)
    assert len(html_ids) == len(set(html_ids)), f"Duplicate IDs in index.html: {[x for x in html_ids if html_ids.count(x) > 1]}"
    inline_gd_ids = set(re.findall(r'\bid="([^"$]+)"', grid_js))
    overlap = set(html_ids) & inline_gd_ids
    assert not overlap, f"Inline grid designer IDs collide with index.html: {overlap}"

    # Overall photo viewer remove button is wired in index.html & views.js, and duplicated client synonym table is gone
    assert "deleteDrawerPhoto(" in views_js
    assert re.search(r"onclick=.*overallViewerRemovePhoto\(", html), "overallViewerRemovePhoto must be wired to UI button"
    assert "drawer-compact-slide-del" not in views_js
    assert "SEARCH_STOP_WORDS" not in views_js
    assert "SEARCH_SYNONYMS" not in views_js

    # Upload flows in modals.js and views.js use apiUpload instead of raw fetch POST
    assert "fetch(`/api/v1/locations/${locId}/photos`" not in modals_js
    assert "fetch(`/api/v1/locations/${locId}/analyze-photo`" not in modals_js
    assert "fetch(`/api/v1/locations/${drawerId}/photos`" not in views_js
    assert "fetch(`/api/v1/items/${itemId}/attachments`" not in views_js


def test_uploads_static_serving_and_connection_refused_error_handling(client):
    import io
    import shutil
    import subprocess
    from pathlib import Path
    from PIL import Image

    # 1. End-to-end capture session creation, QR serving, photo upload, and HTTP GET /uploads/...
    loc = client.post("/api/v1/locations", json={"name": "Bench Drawer", "location_type": "bin"}).json()
    sess_res = client.post(
        "/api/v1/photos/capture-sessions",
        json={"label": "Bench Drawer", "location_id": loc["id"]},
    )
    assert sess_res.status_code == 201
    sess = sess_res.json()

    qr_res = client.get(sess["qr_url"])
    assert qr_res.status_code == 200
    assert qr_res.headers["content-type"].startswith("image/png")
    assert len(qr_res.content) > 100

    buf = io.BytesIO()
    Image.new("RGB", (80, 60), color=(40, 120, 200)).save(buf, format="JPEG")
    buf.seek(0)
    up_res = client.post(
        f"/api/v1/photos/capture-sessions/{sess['token']}/upload",
        files={"file": ("phone_snap.jpg", buf, "image/jpeg")},
    )
    assert up_res.status_code == 200
    up_data = up_res.json()

    orig_get = client.get(up_data["original_path"])
    assert orig_get.status_code == 200
    assert orig_get.headers["content-type"].startswith("image/jpeg")
    assert len(orig_get.content) > 100

    thumb_get = client.get(up_data["thumbnail_path"])
    assert thumb_get.status_code == 200
    assert thumb_get.headers["content-type"].startswith("image/webp")
    assert len(thumb_get.content) > 50

    # 2. Frontend apiFetch translates TypeError('Failed to fetch') (ERR_CONNECTION_REFUSED) into actionable message
    node_exe = shutil.which("node")
    assert node_exe is not None, "node is required for frontend module regression test"
    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    const els = {{
      'phone-photo-modal': {{ classList: {{ add() {{}}, remove() {{}} }} }},
      'phone-photo-status': {{ textContent: '', className: '' }},
      'phone-photo-retry-btn': {{ style: {{ display: 'none' }} }},
    }};
    globalThis.BroadcastChannel = undefined;
    globalThis.window = {{ location: {{ host: 'localhost:8000' }}, addEventListener() {{}} }};
    globalThis.document = {{
      getElementById: (id) => els[id] || null,
      querySelectorAll: () => [],
      addEventListener() {{}},
    }};
    globalThis.fetch = async () => {{ throw new TypeError('Failed to fetch'); }};
    const {{ openPhonePhotoModal }} = await import('file://{js_dir}/phone-capture.js');
    await openPhonePhotoModal({{ inputId: 'item-photo-input', label: 'Take a photo' }});
    const statusText = els['phone-photo-status'].textContent;
    if (!statusText || statusText === 'Failed to fetch' || !statusText.includes('localhost:8000')) {{
      throw new Error('Expected actionable server unreachable message in modal, got: ' + statusText);
    }}
    if (els['phone-photo-retry-btn'].style.display !== 'inline-flex') {{
      throw new Error('Expected retry button to become visible on connection failure');
    }}
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Frontend connection-refused test failed:\n{proc.stderr}"


async def test_analyze_drawer_photo_releases_sqlite_write_lock_during_vision_call(tmp_path):
    import io
    from unittest.mock import patch
    from fastapi import UploadFile
    from PIL import Image
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.db.migrations import run_migrations
    from app.models.item import Item
    from app.models.location import Location, LocationPhoto
    from app.schemas.item import VisionAnalysisResponse
    from app.services.location_service import location_service
    from app.services.vision_service import vision_service

    db_file = tmp_path / "concurrency_lock_test.db"
    file_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 0.15},
    )

    @event.listens_for(file_engine, "connect")
    def _set_pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=150")
        cur.close()

    run_migrations(file_engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=file_engine)

    try:
        with SessionFactory() as setup_db:
            drawer = Location(name="Drawer Lock Test", location_type="drawer")
            setup_db.add(drawer)
            setup_db.commit()
            drawer_id = drawer.id

        concurrent_write_succeeded = False
        photo_committed_before_llm = False

        async def slow_analyze_with_concurrent_writer(image_path, provider=None, model=None):
            nonlocal concurrent_write_succeeded, photo_committed_before_llm
            assert image_path is not None and image_path.exists()
            with SessionFactory() as writer_db:
                persisted_photos = (
                    writer_db.query(LocationPhoto)
                    .filter(LocationPhoto.location_id == drawer_id)
                    .all()
                )
                photo_committed_before_llm = len(persisted_photos) == 1
                writer_db.add(Item(name="Concurrent Item During Vision Scan", location_id=drawer_id))
                writer_db.commit()
                concurrent_write_succeeded = True
            return VisionAnalysisResponse(
                status="success",
                provider_used="local",
                model_used="test",
                items=[],
                message="ok",
                instructions="",
            )

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color=(30, 60, 90)).save(buf, format="JPEG")
        buf.seek(0)
        upload = UploadFile(filename="scan_lock.jpg", file=buf)

        with SessionFactory() as req_db, patch.object(
            vision_service, "analyze_image", side_effect=slow_analyze_with_concurrent_writer
        ):
            res = await location_service.analyze_drawer_photo(
                db=req_db,
                location_id=drawer_id,
                file=upload,
                provider="local",
            )
            req_db.commit()

        assert res.status == "success"
        assert photo_committed_before_llm is True
        assert concurrent_write_succeeded is True
    finally:
        file_engine.dispose()


def test_capture_session_upload_service_layer_and_db_commit_atomicity(client, monkeypatch):
    import inspect
    import io
    from PIL import Image
    from app.api import photos as photos_router
    from app.core.config import settings
    from app.services.capture_session_service import capture_session_service
    from app.services.location_service import location_service

    # Router should not reach directly into repositories
    router_src = inspect.getsource(photos_router)
    assert "location_repository" not in router_src

    drawer = client.post("/api/v1/locations", json={"name": "Atomicity Bin", "location_type": "bin"}).json()
    sess = client.post(
        "/api/v1/photos/capture-sessions",
        json={"label": "Atomicity Bin", "location_id": drawer["id"]},
    ).json()
    token = sess["token"]

    before_originals = set(settings.ORIGINALS_DIR.glob("*"))
    before_thumbs = set(settings.THUMBNAILS_DIR.glob("*"))

    def failing_attach(*args, **kwargs):
        raise RuntimeError("Simulated DB failure while attaching capture photo")

    monkeypatch.setattr(location_service, "attach_photo", failing_attach)

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(200, 40, 40)).save(buf, format="JPEG")
    buf.seek(0)

    try:
        resp = client.post(
            f"/api/v1/photos/capture-sessions/{token}/upload",
            files={"file": ("fail.jpg", buf, "image/jpeg")},
        )
        assert resp.status_code >= 500
    except RuntimeError:
        pass

    # Session must NOT expose an uncommitted/rolled-back photo, and files must be cleaned up
    snap = capture_session_service.get_session(token)
    assert snap.status == "pending"
    assert snap.photos == []
    assert set(settings.ORIGINALS_DIR.glob("*")) == before_originals
    assert set(settings.THUMBNAILS_DIR.glob("*")) == before_thumbs


def test_concurrent_health_checks_use_distinct_probe_files(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from app.api.system import _check_uploads_writable
    from app.core.config import settings

    seen_probes = []
    orig_write_bytes = Path.write_bytes

    def tracking_write_bytes(self: Path, data: bytes):
        if self.name.startswith(".health_probe_"):
            seen_probes.append(self.name)
        return orig_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", tracking_write_bytes)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _check_uploads_writable(), range(12)))

    assert all(results)
    assert len(seen_probes) == 12
    assert len(set(seen_probes)) == 12, f"Health check reused shared probe filenames across concurrent calls: {seen_probes}"
    assert list(settings.UPLOAD_DIR.glob(".health_probe_*")) == []


def test_attachment_image_reports_actual_size_bytes(client):
    import io
    from PIL import Image
    from app.core.config import settings

    item = client.post("/api/v1/items", json={"name": "Logic Analyzer"}).json()
    buf = io.BytesIO()
    Image.new("RGB", (120, 90), color=(40, 90, 140)).save(buf, format="PNG")
    buf.seek(0)

    att_resp = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("wiring.png", buf, "image/png")},
    )
    assert att_resp.status_code == 201
    att_data = att_resp.json()
    disk_file = settings.ORIGINALS_DIR / att_data["file_path"].split("/")[-1]
    assert disk_file.exists()
    assert att_data["size_bytes"] == disk_file.stat().st_size
    assert att_data["size_bytes"] > 0


def test_grid_layout_compartment_and_cubby_slot_types(client):
    from pathlib import Path

    unit = client.post("/api/v1/locations", json={"name": "Cubby Shelf", "location_type": "container"}).json()
    grid_resp = client.put(
        f"/api/v1/locations/{unit['id']}/grid",
        json={
            "grid_rows": 1,
            "grid_cols": 2,
            "slots": [
                {
                    "name": "Compartment 1",
                    "location_type": "compartment",
                    "grid_row": 1,
                    "grid_col": 1,
                    "grid_row_span": 1,
                    "grid_col_span": 1,
                },
                {
                    "name": "Cubby 2",
                    "location_type": "cubby",
                    "grid_row": 1,
                    "grid_col": 2,
                    "grid_row_span": 1,
                    "grid_col_span": 1,
                },
            ],
        },
    )
    assert grid_resp.status_code == 200, grid_resp.text
    children = {c["name"]: c["location_type"] for c in grid_resp.json()["children"]}
    assert children == {"Compartment 1": "compartment", "Cubby 2": "cubby"}

    grid_js = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "grid-designer.js").read_text(
        encoding="utf-8"
    )
    assert 'value="compartment"' in grid_js
    assert 'value="cubby"' in grid_js


def test_network_ipv6_loopback_resolution():
    from app.core.network import get_lan_ip, resolve_effective_base_url

    lan_ip = get_lan_ip()
    resolved_v6 = resolve_effective_base_url(host_header="[::1]:8000")
    assert resolved_v6 == f"http://{lan_ip}:8000"


def test_frontend_unsaved_guard_escape_polling_and_rapid_modal_regressions():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    modals_src = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "modals.js").read_text(
        encoding="utf-8"
    )
    # Verify AI scan passes extended timeout (>=120s) to apiUpload
    assert "timeoutMs" in modals_src

    script = f"""
    const createdBlobUrls = [];
    const revokedBlobUrls = [];
    let blobSeq = 0;
    globalThis.URL.createObjectURL = () => {{
      const u = `blob:test-${{++blobSeq}}`;
      createdBlobUrls.push(u);
      return u;
    }};
    globalThis.URL.revokeObjectURL = (u) => {{
      revokedBlobUrls.push(u);
    }};
    globalThis.BroadcastChannel = undefined;
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};

    const windowListeners = {{}};
    globalThis.window = {{
      location: {{ host: 'localhost:8000', hash: '#/', origin: 'http://localhost:8000' }},
      addEventListener(ev, fn) {{ (windowListeners[ev] ||= []).push(fn); }},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
      confirm: () => true,
    }};
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
        style: {{ display: '' }},
        dataset: {{}},
        disabled: false,
        checked: false,
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
        appendChild() {{}},
        removeAttribute() {{}},
        addEventListener() {{}},
        querySelector() {{ return null; }},
        ...extra,
      }};
    }}

    let rapidSubmitClicked = 0;
    const rapidSubmitBtn = makeEl('rapid-submit-btn', {{
      click() {{ rapidSubmitClicked += 1; }},
    }});
    const rapidModal = makeEl('drawer-rapid-entry-modal', {{
      querySelector(sel) {{
        if (sel.includes('form')) return null;
        if (sel.includes('rapid-submit-btn')) return rapidSubmitBtn;
        return null;
      }},
    }});
    const unsavedModal = makeEl('unsaved-confirm-modal');
    const addItemModal = makeEl('add-item-modal');

    const elements = {{
      'unsaved-confirm-modal': unsavedModal,
      'unsaved-confirm-msg': makeEl('unsaved-confirm-msg'),
      'drawer-rapid-entry-modal': rapidModal,
      'rapid-submit-btn': rapidSubmitBtn,
      'rapid-location-select': makeEl('rapid-location-select', {{ value: '7' }}),
      'rapid-items-text': makeEl('rapid-items-text', {{ value: '' }}),
      'rapid-category-input': makeEl('rapid-category-input', {{ value: '' }}),
      'rapid-photo-filename': makeEl('rapid-photo-filename'),
      'rapid-photo-preview-wrap': makeEl('rapid-photo-preview-wrap'),
      'rapid-photo-input': makeEl('rapid-photo-input'),
      'rapid-ai-results': makeEl('rapid-ai-results'),
      'add-item-modal': addItemModal,
      'item-modal-title': makeEl('item-modal-title'),
      'item-submit-btn': makeEl('item-submit-btn'),
      'edit-item-id': makeEl('edit-item-id'),
      'item-name': makeEl('item-name'),
      'item-quantity': makeEl('item-quantity'),
      'item-unit': makeEl('item-unit'),
      'item-category': makeEl('item-category'),
      'item-serial': makeEl('item-serial'),
      'item-tags': makeEl('item-tags'),
      'item-description': makeEl('item-description'),
      'item-photo-input': makeEl('item-photo-input'),
      'item-photo-input-label': makeEl('item-photo-input-label'),
      'item-location-select': makeEl('item-location-select'),
      'phone-photo-modal': makeEl('phone-photo-modal'),
      'phone-photo-title': makeEl('phone-photo-title'),
      'phone-photo-qr-img': makeEl('phone-photo-qr-img'),
      'phone-photo-status': makeEl('phone-photo-status'),
      'phone-photo-retry-btn': makeEl('phone-photo-retry-btn'),
      'phone-photo-url-hint': makeEl('phone-photo-url-hint'),
      'phone-photo-computer-btn': makeEl('phone-photo-computer-btn'),
      'phone-photo-cancel-btn': makeEl('phone-photo-cancel-btn'),
      'phone-photo-done-btn': makeEl('phone-photo-done-btn'),
      'phone-photo-scan-btn': makeEl('phone-photo-scan-btn'),
      'phone-photo-received-wrap': makeEl('phone-photo-received-wrap'),
      'phone-photo-received-count': makeEl('phone-photo-received-count'),
      'phone-photo-received-thumbs': makeEl('phone-photo-received-thumbs'),
      'toast-shelf': makeEl('toast-shelf'),
    }};

    globalThis.document = {{
      readyState: 'complete',
      documentElement: {{ setAttribute() {{}} }},
      activeElement: null,
      fullscreenElement: null,
      getElementById: (id) => elements[id] || null,
      querySelector: (sel) => {{
        if (sel === '#drawer-rapid-entry-modal form') return null;
        return null;
      }},
      querySelectorAll: (sel) => {{
        if (sel.includes('.modal-scrim.open')) {{
          return [unsavedModal, rapidModal, addItemModal].filter(m => m.classList.contains('open'));
        }}
        return [];
      }},
      addEventListener() {{}},
      createElement: (tag) => makeEl(tag),
    }};

    // 1. Test guardModalClose('drawer-rapid-entry-modal') with 'save' triggers rapid-submit-btn
    const guard = await import('file://{js_dir}/unsaved-guard.js');
    rapidModal.classList.add('open');
    rapidModal.dataset.dirty = 'true';
    const closePromise = guard.guardModalClose('drawer-rapid-entry-modal', () => {{
      rapidModal.classList.remove('open');
    }});
    await new Promise(r => setTimeout(r, 10));
    guard.resolveUnsavedPrompt('save');
    const saveHandled = await closePromise;
    if (!saveHandled || rapidSubmitClicked !== 1) {{
      throw new Error(`Expected Save on rapid modal to click #rapid-submit-btn, got saveHandled=${{saveHandled}}, clicks=${{rapidSubmitClicked}}`);
    }}

    // 2. Test apiUpload forwards custom timeoutMs
    let abortSignalAborted = false;
    globalThis.fetch = async (_url, opts) => {{
      await new Promise((resolve, reject) => {{
        const t = setTimeout(resolve, 60);
        opts.signal.addEventListener('abort', () => {{
          clearTimeout(t);
          abortSignalAborted = true;
          const e = new Error('Aborted');
          e.name = 'AbortError';
          reject(e);
        }});
      }});
      return {{ ok: true, status: 200, json: async () => ({{ ok: true }}) }};
    }};
    const api = await import('file://{js_dir}/api.js');
    let timedOutShort = false;
    try {{
      await api.apiUpload('/api/v1/test', new FormData(), 'err', {{ timeoutMs: 15 }});
    }} catch {{
      timedOutShort = true;
    }}
    if (!timedOutShort || !abortSignalAborted) {{
      throw new Error('Expected apiUpload to honor custom options.timeoutMs');
    }}

    // 3. Test multi-photo blob URL cleanup in modals.js
    const stateMod = await import('file://{js_dir}/state.js');
    const modals = await import('file://{js_dir}/modals.js');
    modals.handleRapidPhotoSelected({{ target: {{ files: [{{ name: 'a.jpg' }}, {{ name: 'b.jpg' }}, {{ name: 'c.jpg' }}] }} }});
    modals.handleRapidPhotoSelected({{ target: {{ files: [{{ name: 'd.jpg' }}] }} }});
    if (revokedBlobUrls.length < 3) {{
      throw new Error(`Expected all 3 previous blob URLs to be revoked, only revoked ${{revokedBlobUrls.length}}: ${{revokedBlobUrls}}`);
    }}

    // 4. Test chooseAddAction('manual') when viewing an item uses item.location_id instead of item.id
    stateMod.state.locationMap.set(7, {{ id: 7, name: 'Bin 7', full_path: 'Room > Bin 7', location_type: 'bin' }});
    stateMod.state.locationMap.set(42, {{ id: 42, name: 'Wrong Room 42', full_path: 'Wrong Room 42', location_type: 'room' }});
    stateMod.state.items = [{{ id: 42, name: 'Caliper', location_id: 7 }}];
    stateMod.state.currentView = 'item';
    stateMod.state.currentLocationId = 42;
    modals.chooseAddAction('manual');
    if (elements['item-location-select'].value !== '7') {{
      throw new Error(`Expected item-location-select to prefill item's location_id '7', got '${{elements['item-location-select'].value}}'`);
    }}
    addItemModal.classList.remove('open');

    // 5. Test phone-capture.js does not overlap concurrent poll requests when response > 800ms
    let concurrentPolls = 0;
    let maxConcurrentPolls = 0;
    globalThis.fetch = async (url, _opts) => {{
      if (String(url).endsWith('/capture-sessions')) {{
        return {{
          ok: true,
          status: 201,
          json: async () => ({{ token: 'tok-overlap', qr_url: '/qr', capture_url: 'http://x/c' }}),
        }};
      }}
      concurrentPolls += 1;
      if (concurrentPolls > maxConcurrentPolls) maxConcurrentPolls = concurrentPolls;
      await new Promise(r => setTimeout(r, 1100));
      concurrentPolls -= 1;
      return {{
        ok: true,
        status: 200,
        json: async () => ({{ token: 'tok-overlap', status: 'pending', photos: [] }}),
      }};
    }};
    const phoneCapture = await import('file://{js_dir}/phone-capture.js');
    await phoneCapture.openPhonePhotoModal({{ inputId: 'rapid-photo-input', label: 'Scan' }});
    await new Promise(r => setTimeout(r, 1950));
    phoneCapture.closePhonePhotoModal();
    if (maxConcurrentPolls > 1) {{
      throw new Error(`Expected at most 1 in-flight poll request, saw maxConcurrentPolls=${{maxConcurrentPolls}}`);
    }}

    // 6. Test desktop handoff on rapid-photo-input attaches rapidRemotePhotoPath on submitRapidEntry
    let attachedLocations = [];
    globalThis.Image = class {{
      constructor() {{ this.naturalWidth = 100; this.naturalHeight = 100; }}
      set src(_v) {{ if (this.onload) this.onload(); }}
    }};
    elements['photo-editor-modal'] = makeEl('photo-editor-modal');
    elements['photo-editor-title'] = makeEl('photo-editor-title');
    globalThis.fetch = async (url, opts) => {{
      const u = String(url);
      if (u.endsWith('/capture-sessions')) {{
        return {{
          ok: true,
          status: 201,
          json: async () => ({{ token: 'tok-rapid', qr_url: '/qr', capture_url: 'http://x/c' }}),
        }};
      }}
      if (u.includes('/capture-sessions/tok-rapid')) {{
        return {{
          ok: true,
          status: 200,
          json: async () => ({{
            token: 'tok-rapid',
            status: 'uploaded',
            photo: {{ original_path: '/uploads/originals/phone.jpg', thumbnail_path: '/uploads/thumbnails/phone_thumb.webp', filename: 'phone.jpg' }},
            photos: [{{ original_path: '/uploads/originals/phone.jpg', thumbnail_path: '/uploads/thumbnails/phone_thumb.webp', filename: 'phone.jpg' }}],
          }}),
        }};
      }}
      if (u.includes('/photos/attach')) {{
        attachedLocations.push({{ url: u, body: JSON.parse(opts.body) }});
        return {{ ok: true, status: 201, json: async () => ({{ id: 99, photo_path: '/uploads/originals/phone.jpg' }}) }};
      }}
      return {{ ok: true, status: 200, json: async () => [] }};
    }};
    phoneCapture.requestPhotoForInput('rapid-photo-input');
    await new Promise(r => setTimeout(r, 900));
    const photoEditor = await import('file://{js_dir}/photo-editor.js');
    await photoEditor.applyPhotoEditor();
    elements['rapid-items-text'].value = '';
    elements['rapid-location-select'].value = '7';
    rapidModal.classList.add('open');
    await modals.submitRapidEntry();
    if (attachedLocations.length !== 1 || attachedLocations[0].body.photo_path !== '/uploads/originals/phone.jpg') {{
      throw new Error(`Expected submitRapidEntry to attach rapidRemotePhotoPath, got ${{JSON.stringify(attachedLocations)}}`);
    }}

    // 7. Test Escape key cancels unsaved-confirm-modal instead of bypassing guard and wiping dirty modal
    await import('file://{js_dir}/app.js');
    addItemModal.classList.add('open');
    addItemModal.dataset.dirty = 'true';
    const escPrompt = guard.showUnsavedPrompt('Unsaved?');
    for (const fn of (windowListeners['keydown'] || [])) {{
      fn({{ key: 'Escape', preventDefault() {{}} }});
    }}
    const escResult = await Promise.race([
      escPrompt,
      new Promise((_, rej) => setTimeout(() => rej(new Error('Escape left showUnsavedPrompt hanging!')), 200)),
    ]);
    if (escResult !== 'cancel' || !addItemModal.classList.contains('open')) {{
      throw new Error(`Expected Escape on unsaved prompt to resolve 'cancel' and keep dirty modal open, got ${{escResult}}`);
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"Frontend regression test failed:\n{proc.stderr}"





def test_mobile_and_tablet_limited_item_and_touch_capture_views(client):
    import io
    from pathlib import Path
    from PIL import Image

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    mobile_js = (static_dir / "js" / "mobile-views.js").read_text(encoding="utf-8")
    views_js = (static_dir / "js" / "views.js").read_text(encoding="utf-8")
    router_js = (static_dir / "js" / "router.js").read_text(encoding="utf-8")
    css = (static_dir / "css" / "app.css").read_text(encoding="utf-8")
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    # Frontend invariants for phone & tablet limited item view + cabinet diagram photo flow
    assert "renderMobileFindView" in mobile_js
    assert "renderMobileCabinetDiagram" in mobile_js
    assert "renderLimitedItemView" in mobile_js
    assert "shouldUseLimitedItemView" in views_js
    assert "shouldUseMobileCompanion" in views_js
    assert "navToTouchCapture" in router_js
    assert "saveTouchDrawerPhotos" in mobile_js
    assert "Save & Next" in mobile_js
    assert "mobile-nav-capture" in html
    assert "Find Item" in html
    assert "Add Photos" in html
    assert ".m-cabinet-frame" in css
    assert ".mobile-limited-grid" in css
    assert ".touch-split-body" in css
    assert ".touch-save-bar" in css

    # End-to-end API flow: Room -> Storage Method -> Drawer 1 & Drawer 2 photos + Item search
    room = client.post("/api/v1/locations", json={"name": "Kitchen", "location_type": "room"}).json()
    unit = client.post("/api/v1/locations", json={"name": "Kitchen Cabinet", "location_type": "container", "parent_id": room["id"]}).json()
    d1 = client.post("/api/v1/locations", json={"name": "Drawer 1", "location_type": "drawer", "parent_id": unit["id"]}).json()
    d2 = client.post("/api/v1/locations", json={"name": "Drawer 2", "location_type": "drawer", "parent_id": unit["id"]}).json()

    img = Image.new("RGB", (40, 40), color=(240, 180, 40))
    for drawer in (d1, d2):
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        up = client.post(
            f"/api/v1/locations/{drawer['id']}/photos",
            files={"file": (f"{drawer['name']}.jpg", buf, "image/jpeg")},
        )
        assert up.status_code == 201

    item = client.post("/api/v1/items", json={"name": "Measuring Spoons", "location_id": d1["id"], "quantity": 1}).json()
    search_res = client.get("/api/v1/search?q=Measuring")
    assert search_res.status_code == 200
    found = search_res.json()["items"]
    assert any(it["id"] == item["id"] for it in found)


def test_svg_attachment_is_served_with_sandbox_csp(client):
    item = client.post("/api/v1/items", json={"name": "Pinout Board"}).json()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(document.cookie)</script></svg>'
    svg_att = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("chart.svg", svg, "image/svg+xml")},
    ).json()
    pdf_att = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("sheet.pdf", b"%PDF-1.4\n%test\n", "application/pdf")},
    ).json()

    svg_res = client.get(svg_att["file_path"])
    assert svg_res.status_code == 200
    assert "sandbox" in svg_res.headers.get("content-security-policy", "")

    # Chrome refuses to render PDFs in sandboxed documents, so PDFs must stay viewable.
    pdf_res = client.get(pdf_att["file_path"])
    assert pdf_res.status_code == 200
    assert "sandbox" not in pdf_res.headers.get("content-security-policy", "")


def test_api_timestamps_are_serialized_as_utc(client):
    from datetime import datetime

    loc = client.post("/api/v1/locations", json={"name": "Shelf", "location_type": "shelf"}).json()
    item = client.post("/api/v1/items", json={"name": "Caliper", "location_id": loc["id"]}).json()
    detail = client.get(f"/api/v1/items/{item['id']}").json()

    stamps = [
        loc["created_at"], loc["updated_at"],
        detail["last_seen_at"], detail["created_at"], detail["updated_at"],
        detail["audit_logs"][0]["recorded_at"],
    ]
    for raw in stamps:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0, raw


def test_item_list_accepts_full_inventory_limit(client):
    # The SPA loads the entire inventory into client state; a low cap silently hides items.
    res = client.get("/api/v1/items?limit=20000")
    assert res.status_code == 200


def test_ai_crop_filenames_are_detectable_as_crops(tmp_path):
    from PIL import Image as PILImage
    from app.services.image_lookup_service import is_crop_or_missing_photo
    from app.services.photo_service import photo_service

    src = tmp_path / "drawer.jpg"
    PILImage.new("RGB", (400, 300), color=(200, 50, 50)).save(src, format="JPEG")
    crop_url = photo_service.crop_and_save(src, [100, 100, 600, 700], coord_mode="norm_1000")

    assert crop_url is not None
    assert is_crop_or_missing_photo(crop_url)
    # Thumbnail lookup and cleanup derive the thumb from the original's stem.
    assert photo_service.delete_upload_url(crop_url)


async def test_downloaded_image_processing_runs_off_event_loop(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.services import image_lookup_service

    ran_on_loop: list[bool] = []

    def fake_process_and_save(upload):
        try:
            asyncio.get_running_loop()
            ran_on_loop.append(True)
        except RuntimeError:
            ran_on_loop.append(False)
        return {"original_path": "/uploads/originals/x.png", "thumbnail_path": None, "filename": "x.png"}

    monkeypatch.setattr(image_lookup_service.photo_service, "process_and_save", fake_process_and_save)

    img_resp = MagicMock(status_code=200, headers={"content-type": "image/png"}, content=b"\x89PNG fake")
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=img_resp)

    saved = await image_lookup_service.download_and_save_image(http_client, "https://upload.wikimedia.org/a.png")
    assert saved == "/uploads/originals/x.png"
    assert ran_on_loop == [False]


async def test_undecodable_downloaded_image_is_skipped_not_raised():
    from unittest.mock import AsyncMock, MagicMock
    from app.services import image_lookup_service

    img_resp = MagicMock(status_code=200, headers={"content-type": "image/png"}, content=b"not an image")
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=img_resp)

    assert await image_lookup_service.download_and_save_image(http_client, "https://upload.wikimedia.org/a.png") is None


def test_search_ranks_direct_name_matches_above_synonym_and_description_hits(client):
    target = client.post("/api/v1/items", json={"name": "12V Power Supply"}).json()
    # Created later, so plain last-seen ordering would put these first.
    client.post("/api/v1/items", json={"name": "USB Cable"}).json()
    client.post("/api/v1/items", json={"name": "LM339", "description": "Comparator, single supply, low power"}).json()

    found = client.get("/api/v1/search?q=power supply").json()["items"]
    assert found[0]["id"] == target["id"]
    names = [it["name"] for it in found]
    assert names.index("LM339") < names.index("USB Cable")


async def test_image_download_does_not_follow_redirect_to_private_host(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from app.services import image_lookup_service

    def fail_process(upload):
        raise AssertionError("private redirect target must not be fetched/saved")

    monkeypatch.setattr(image_lookup_service.photo_service, "process_and_save", fail_process)

    redirect = MagicMock(status_code=302, headers={"location": "https://192.168.1.10/admin.png"}, content=b"")
    redirect.is_redirect = True
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=redirect)

    assert await image_lookup_service.download_and_save_image(http_client, "https://upload.wikimedia.org/a.png") is None
    for call in http_client.get.await_args_list:
        assert call.kwargs.get("follow_redirects") is False


async def test_gemini_api_key_is_sent_in_header_not_url(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.vision_service import vision_service

    resp = MagicMock(status_code=500)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.vision_service.httpx.AsyncClient", return_value=cm):
        await vision_service._analyze_with_gemini("aGVsbG8=", "gemini-test", "SECRET-KEY")

    url = client.post.await_args.args[0]
    headers = client.post.await_args.kwargs.get("headers") or {}
    assert "SECRET-KEY" not in url
    assert headers.get("x-goog-api-key") == "SECRET-KEY"


def test_location_photo_type_is_validated(client):
    import io
    from PIL import Image

    loc = client.post("/api/v1/locations", json={"name": "Bin", "location_type": "bin"}).json()
    res = client.post(
        f"/api/v1/locations/{loc['id']}/photos/attach",
        json={"photo_path": "/uploads/originals/abc.jpg", "photo_type": "bogus"},
    )
    assert res.status_code == 422

    buf = io.BytesIO()
    Image.new("RGB", (40, 40)).save(buf, format="PNG")
    res = client.post(
        f"/api/v1/locations/{loc['id']}/photos",
        files={"file": ("p.png", buf.getvalue(), "image/png")},
        data={"photo_type": "bogus"},
    )
    assert res.status_code == 422
