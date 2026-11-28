import pytest
from app.models.location import Location
from http_mocks import make_http_client
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
    assert "New Space or Container" in html
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


def test_capture_session_snapshot_isolation_and_expired_upload_cleanup(client, db_session, monkeypatch):
    import io
    from datetime import datetime, timedelta, timezone
    from PIL import Image
    from app.core.config import settings
    from app.models.session import CaptureSessionRecord
    from app.services.capture_session_service import capture_session_service

    sess_data = client.post("/api/v1/photos/capture-sessions", json={"label": "Test Bin"}).json()
    token = sess_data["token"]

    # Snapshot returned by get_session must not expose internal mutable photos list
    snap = capture_session_service.get_session(db_session, token)
    snap.photos.append({"filename": "mutated.jpg"})
    assert capture_session_service.get_session(db_session, token).photos == []

    # If session expires while photo_service.process_and_save is running, 410 must not leave orphan files
    before_originals = set(settings.ORIGINALS_DIR.glob("*"))
    before_thumbs = set(settings.THUMBNAILS_DIR.glob("*"))

    orig_process = capture_session_service.mark_uploaded

    def expire_then_mark(db, tok: str, photo_result: dict):
        rec = db.get(CaptureSessionRecord, tok)
        rec.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).replace(tzinfo=None)
        db.flush()
        return orig_process(db, tok, photo_result)

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


def test_capture_session_upload_service_layer_and_db_commit_atomicity(client, db_session, monkeypatch):
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
    snap = capture_session_service.get_session(db_session, token)
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
    # Legacy /items is bounded to <=500, while /items/page provides cursor pagination up to 200 per page.
    assert client.get("/api/v1/items?limit=20000").status_code == 422
    page_res = client.get("/api/v1/items/page?limit=200")
    assert page_res.status_code == 200
    body = page_res.json()
    assert "items" in body and "total" in body and "has_more" in body


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

    async def fake_get(url, **kwargs):
        return img_resp

    http_client = make_http_client(fake_get)

    saved = await image_lookup_service.download_and_save_image(http_client, "https://upload.wikimedia.org/a.png")
    assert saved == "/uploads/originals/x.png"
    assert ran_on_loop == [False]


async def test_undecodable_downloaded_image_is_skipped_not_raised():
    from unittest.mock import AsyncMock, MagicMock
    from app.services import image_lookup_service

    img_resp = MagicMock(status_code=200, headers={"content-type": "image/png"}, content=b"not an image")

    async def fake_get(url, **kwargs):
        return img_resp

    http_client = make_http_client(fake_get)

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
    seen_kwargs: list[dict] = []

    async def fake_get(url, **kwargs):
        seen_kwargs.append(kwargs)
        return redirect

    http_client = make_http_client(fake_get)

    assert await image_lookup_service.download_and_save_image(http_client, "https://upload.wikimedia.org/a.png") is None
    assert seen_kwargs
    for kwargs in seen_kwargs:
        assert kwargs.get("follow_redirects") is False


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


def test_storage_unit_grid_view_overflow_tokens_and_duplicate_coord_regression():
    import re
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    css = (static_dir / "css" / "app.css").read_text(encoding="utf-8")

    # 1. Theme root tokens must define surface variables used by grid slots & dark/light color-scheme
    root_match = re.search(r":root\s*\{([^}]+)\}", css)
    light_match = re.search(r'\[data-theme="light"\]\s*\{([^}]+)\}', css)
    assert root_match and light_match
    assert "color-scheme: dark" in root_match.group(1)
    assert "color-scheme: light" in light_match.group(1)
    defined_tokens = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", root_match.group(1)))
    assert "--bg-surface" in defined_tokens
    assert "--bg-surface-elevated" in defined_tokens

    # 2. .cgrid-slot must clip overflow and .cgrid-slot-info must shrink within narrow columns
    slot_block = re.search(r"\.cgrid-slot\s*\{([^}]+)\}", css)
    assert slot_block and "overflow: hidden" in slot_block.group(1)
    info_block = re.search(r"\.cgrid-slot-info\s*\{([^}]+)\}", css)
    assert info_block and "flex: 0 0 auto" not in info_block.group(1)

    # 3. Behavioral Node check: no duplicate coord labels on empty/coord-named drawers,
    #    item name surfaced when drawer name equals coord, and 1-photo carousel never shows scroll arrows
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (static_dir / "js").as_posix()
    script = f"""
    globalThis.BroadcastChannel = undefined;
    globalThis.window = {{
      location: {{ host: 'localhost:8000', hash: '#/', origin: 'http://localhost:8000' }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
    }};
    globalThis.document = {{
      querySelectorAll: () => [],
      getElementById: () => null,
    }};
    globalThis.requestAnimationFrame = () => {{}};

    const stateMod = await import('file://{js_dir}/state.js');
    const gridMod = await import('file://{js_dir}/grid-designer.js');
    const carouselMod = await import('file://{js_dir}/carousel.js');

    const unit = {{
      id: 10,
      name: 'Yellow storage organizer',
      location_type: 'container',
      grid_rows: 6,
      grid_cols: 8,
      children: [
        {{
          id: 101,
          name: 'H1',
          location_type: 'drawer',
          parent_id: 10,
          grid_row: 1,
          grid_col: 8,
          grid_row_span: 1,
          grid_col_span: 1,
          total_item_count: 1,
          children: [],
          photos: [{{ id: 1, photo_path: '/uploads/originals/h1.jpg' }}],
        }},
        {{
          id: 102,
          name: 'A5–B6',
          location_type: 'drawer',
          parent_id: 10,
          grid_row: 5,
          grid_col: 1,
          grid_row_span: 2,
          grid_col_span: 2,
          total_item_count: 0,
          children: [],
          photos: [],
        }},
      ],
    }};
    stateMod.state.locations = [unit];
    stateMod.state.items = [
      {{ id: 901, name: 'OLED 0.96 Display', location_id: 101, quantity: 1 }},
    ];
    stateMod.indexLocations();

    const html = gridMod.renderContainerGridHtml(unit);

    // Empty merged drawer A5–B6 must NOT render 'A5–B6' twice inside its card
    const a5Match = html.match(/id="drawer-card-102"[\\s\\S]*?<\\/div>\\s*(?=<div|$)/);
    const a5Occurrences = (html.match(/>A5–B6</g) || []).length;
    if (a5Occurrences !== 1) {{
      throw new Error(`Expected A5–B6 text once, got ${{a5Occurrences}}`);
    }}

    // Coord-named drawer H1 with an item must surface the item name ('OLED 0.96 Display') instead of repeating 'H1'
    if (!html.includes('OLED 0.96 Display')) {{
      throw new Error('Expected coord-named drawer H1 to surface its item name');
    }}
    const h1Occurrences = (html.match(/>H1</g) || []).length;
    if (h1Occurrences !== 1) {{
      throw new Error(`Expected H1 text once, got ${{h1Occurrences}}`);
    }}

    // Single-photo carousel must never toggle can-next / is-overflowing even in narrow cells
    const classes = new Set();
    const wrapMock = {{
      classList: {{
        toggle(cls, on) {{ if (on) classes.add(cls); else classes.delete(cls); }},
      }},
    }};
    const singleItemTrack = {{
      scrollWidth: 58,
      clientWidth: 32,
      scrollLeft: 0,
      children: [{{}}],
      closest: () => wrapMock,
    }};
    carouselMod.syncSlotCarousel(singleItemTrack);
    if (classes.has('can-next') || classes.has('is-overflowing')) {{
      throw new Error('Single-thumbnail carousel should never render overflow arrows');
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Grid view regression check failed:\n{proc.stderr}"


def test_grid_slot_ensure_idempotent_bounds_overlap_and_box_preservation(client):
    room = client.post("/api/v1/locations", json={"name": "Office", "location_type": "room"}).json()
    unit = client.post(
        "/api/v1/locations",
        json={
            "name": "Yellow storage organizer",
            "location_type": "container",
            "parent_id": room["id"],
            "grid_rows": 4,
            "grid_cols": 4,
        },
    ).json()

    a1 = client.post(
        "/api/v1/locations",
        json={
            "name": "Stepper Drivers",
            "location_type": "compartment",
            "parent_id": unit["id"],
            "grid_row": 1,
            "grid_col": 1,
            "grid_row_span": 1,
            "grid_col_span": 1,
        },
    ).json()
    merged = client.post(
        "/api/v1/locations",
        json={
            "name": "A3–B4",
            "location_type": "compartment",
            "parent_id": unit["id"],
            "grid_row": 3,
            "grid_col": 1,
            "grid_row_span": 2,
            "grid_col_span": 2,
        },
    ).json()
    sub_box = client.post(
        "/api/v1/locations",
        json={
            "name": "JST Connectors Kit",
            "location_type": "box",
            "parent_id": unit["id"],
        },
    ).json()

    # 1. Clicking empty grid coordinate B1 creates compartment idempotently
    b1_resp = client.post(
        f"/api/v1/locations/{unit['id']}/grid/slot",
        json={"grid_row": 1, "grid_col": 2},
    )
    assert b1_resp.status_code in (200, 201), b1_resp.text
    b1 = b1_resp.json()
    assert b1["name"] == "B1"
    assert b1["location_type"] == "compartment"
    assert b1["grid_row"] == 1 and b1["grid_col"] == 2

    # Repeat call on same coordinate (or inside an existing multi-cell span) returns existing compartment without duplicating
    b1_again = client.post(
        f"/api/v1/locations/{unit['id']}/grid/slot",
        json={"grid_row": 1, "grid_col": 2},
    )
    assert b1_again.status_code == 200
    assert b1_again.json()["id"] == b1["id"]

    span_hit = client.post(
        f"/api/v1/locations/{unit['id']}/grid/slot",
        json={"grid_row": 4, "grid_col": 2},
    )
    assert span_hit.status_code == 200
    assert span_hit.json()["id"] == merged["id"]

    # 2. Out-of-bounds and overlap validation enforced across slot endpoint, create, and update
    oob = client.post(
        f"/api/v1/locations/{unit['id']}/grid/slot",
        json={"grid_row": 5, "grid_col": 1},
    )
    assert oob.status_code == 400

    dup_create = client.post(
        "/api/v1/locations",
        json={
            "name": "Duplicate A1",
            "location_type": "compartment",
            "parent_id": unit["id"],
            "grid_row": 1,
            "grid_col": 1,
        },
    )
    assert dup_create.status_code == 400

    dup_patch = client.patch(
        f"/api/v1/locations/{b1['id']}",
        json={"grid_row": 1, "grid_col": 1},
    )
    assert dup_patch.status_code == 400

    # 3. Saving grid layout preserves unplaced 'box' / 'kit' subcontainers under the container
    save_res = client.put(
        f"/api/v1/locations/{unit['id']}/grid",
        json={
            "grid_rows": 4,
            "grid_cols": 4,
            "slots": [
                {
                    "id": a1["id"],
                    "name": "Stepper Drivers",
                    "location_type": "compartment",
                    "grid_row": 1,
                    "grid_col": 1,
                    "grid_row_span": 1,
                    "grid_col_span": 1,
                },
                {
                    "id": b1["id"],
                    "name": "Buck Converters",
                    "location_type": "compartment",
                    "grid_row": 1,
                    "grid_col": 2,
                    "grid_row_span": 1,
                    "grid_col_span": 1,
                },
            ],
        },
    )
    assert save_res.status_code == 200
    child_ids = {c["id"] for c in save_res.json()["children"]}
    assert sub_box["id"] in child_ids, "Unplaced Box/Kit must not be deleted when saving container grid layout"


def test_compartment_grid_inline_and_page_workflow_behavioral():
    import re
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    js_dir = static_dir / "js"
    comp_js_path = js_dir / "compartment-actions.js"
    assert comp_js_path.exists(), "Expected modular compartment-actions.js"

    sw_src = (static_dir / "sw.js").read_text(encoding="utf-8")
    assert "/js/compartment-actions.js" in sw_src

    # Verify every inline handler in compartment-actions.js, grid-designer.js, and views.js is exposed on window in app.js
    app_js = (js_dir / "app.js").read_text(encoding="utf-8")
    window_block_match = re.search(r"Object\.assign\(\s*window\s*,\s*\{([^}]+)\}", app_js, re.DOTALL)
    assert window_block_match
    exported_names = set(re.findall(r"\b([a-zA-Z0-9_]+)\b", window_block_match.group(1)))
    builtin_inline = {"if", "event", "window", "return"}
    for fname in ("compartment-actions.js", "grid-designer.js", "views.js"):
        src = (js_dir / fname).read_text(encoding="utf-8")
        used = set(re.findall(r'on(?:click|keydown|change|input|submit)=\s*"(?:event\.stopPropagation\(\);\s*)?([a-zA-Z0-9_]+)\s*\(', src))
        missing = (used - exported_names) - builtin_inline
        assert not missing, f"Inline handlers in {fname} not exposed on window in app.js: {missing}"

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_posix = js_dir.as_posix()
    script = f"""
    globalThis.BroadcastChannel = undefined;
    globalThis.URL.createObjectURL = () => 'blob:test';
    globalThis.URL.revokeObjectURL = () => {{}};
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.requestAnimationFrame = (fn) => fn && fn();

    const elements = {{}};
    function makeEl(id = '') {{
      return {{
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        hidden: true,
        open: false,
        style: {{}},
        dataset: {{}},
        files: [],
        scrollTop: 0,
        classList: {{
          _s: new Set(),
          add(...c) {{ c.forEach(x => this._s.add(x)); }},
          remove(...c) {{ c.forEach(x => this._s.delete(x)); }},
          toggle(c, f) {{
            const on = f === undefined ? !this._s.has(c) : Boolean(f);
            if (on) this._s.add(c); else this._s.delete(c);
            return on;
          }},
          contains(c) {{ return this._s.has(c); }},
        }},
        setAttribute() {{}},
        appendChild() {{}},
        remove() {{}},
        focus() {{}},
        select() {{}},
        click() {{}},
        scrollIntoView() {{}},
        querySelector: () => null,
        querySelectorAll: () => [],
      }};
    }}
    function getEl(id) {{
      if (!elements[id]) elements[id] = makeEl(id);
      return elements[id];
    }}

    globalThis.window = {{
      location: {{ host: 'localhost:8000', hash: '#/unit/10', origin: 'http://localhost:8000' }},
      history: {{ pushState(_s, _t, h) {{ globalThis.window.location.hash = h; }} }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
      confirm: () => true,
    }};
    Object.defineProperty(globalThis, 'navigator', {{
      value: {{ userAgent: 'Mozilla/5.0 Windows NT 10.0', maxTouchPoints: 0 }},
      configurable: true,
    }});
    globalThis.window.navigator = globalThis.navigator;
    globalThis.history = globalThis.window.history;
    globalThis.document = {{
      getElementById: (id) => getEl(id),
      createElement: (tag) => makeEl(tag),
      body: makeEl('body'),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
      documentElement: makeEl('html'),
      activeElement: null,
    }};

    let mockLocations = [
      {{
        id: 1,
        name: 'Office',
        location_type: 'room',
        parent_id: null,
        full_path: 'Office',
        total_item_count: 1,
        child_count: 1,
        photos: [],
        children: [
          {{
            id: 10,
            name: 'Yellow storage organizer',
            location_type: 'container',
            parent_id: 1,
            grid_rows: 2,
            grid_cols: 2,
            full_path: 'Office > Yellow storage organizer',
            total_item_count: 1,
            child_count: 1,
            photos: [],
            children: [
              {{
                id: 101,
                name: 'A1',
                location_type: 'compartment',
                parent_id: 10,
                grid_row: 1,
                grid_col: 1,
                grid_row_span: 1,
                grid_col_span: 1,
                full_path: 'Office > Yellow storage organizer > A1',
                total_item_count: 1,
                child_count: 0,
                photos: [],
                children: [],
              }},
            ],
          }},
        ],
      }},
    ];
    let mockItems = [
      {{ id: 500, name: 'Stepper Driver', quantity: 1, location_id: 101 }},
    ];

    const calls = [];
    globalThis.fetch = async (url, opts = {{}}) => {{
      const method = (opts.method || 'GET').toUpperCase();
      const u = String(url);
      calls.push({{ method, url: u, body: opts.body }});

      if (u.includes('/api/v1/locations/tree')) {{
        return {{ ok: true, status: 200, json: async () => mockLocations }};
      }}
      if (u.startsWith('/api/v1/items/page')) {{
        return {{ ok: true, status: 200, json: async () => ({{ items: mockItems, total: mockItems.length, limit: 200, next_cursor: null, has_more: false }}) }};
      }}
      if (u.startsWith('/api/v1/items?')) {{
        return {{ ok: true, status: 200, json: async () => mockItems }};
      }}
      if (u === '/api/v1/locations/10/grid/slot' && method === 'POST') {{
        const payload = JSON.parse(opts.body);
        const created = {{
          id: 102,
          name: 'B1',
          location_type: 'compartment',
          parent_id: 10,
          grid_row: payload.grid_row,
          grid_col: payload.grid_col,
          grid_row_span: 1,
          grid_col_span: 1,
          full_path: 'Office > Yellow storage organizer > B1',
          total_item_count: 0,
          child_count: 0,
          photos: [{{ id: 77, photo_path: '/uploads/originals/b1.jpg' }}],
          children: [],
        }};
        mockLocations[0].children[0].children.push(created);
        return {{ ok: true, status: 201, json: async () => created }};
      }}
      if (u.startsWith('/api/v1/locations/') && method === 'PATCH') {{
        const locId = Number(u.split('/').pop());
        const payload = JSON.parse(opts.body);
        const unitChildren = mockLocations[0].children[0].children;
        const found = unitChildren.find(c => c.id === locId);
        if (found && payload.name) found.name = payload.name;
        return {{ ok: true, status: 200, json: async () => found || {{ id: locId, ...payload }} }};
      }}
      if (u === '/api/v1/items/bulk' && method === 'POST') {{
        const payload = JSON.parse(opts.body);
        const created = [{{ id: 601, name: 'XL4015 Buck Module', quantity: 2, location_id: payload.location_id }}];
        mockItems.push(...created);
        return {{ ok: true, status: 201, json: async () => created }};
      }}
      if (u.includes('/analyze-photo') && method === 'POST') {{
        return {{
          ok: true,
          status: 200,
          json: async () => ({{
            status: 'success',
            message: 'Detected 1 item',
            suggested_group_name: 'Power Modules',
            items: [{{ name: 'MP1584EN Step Down', estimated_qty: 3, part_number: 'MP1584EN' }}],
          }}),
        }};
      }}
      if (u.includes('/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => ({{ default_provider: 'local', providers: [] }}) }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const stateMod = await import('file://{js_posix}/state.js');
    const views = await import('file://{js_posix}/views.js');
    const comp = await import('file://{js_posix}/compartment-actions.js');
    const router = await import('file://{js_posix}/router.js');
    globalThis.window.renderCurrentWorkspace = router.renderCurrentWorkspace;

    await stateMod.loadAllData();
    router.navToStorageUnit(10, false);
    const unitHtml = getEl('app-workspace').innerHTML;

    // 1. Empty coordinate B1 (row 1, col 2) is clickable to open/create compartment
    if (!unitHtml.includes('openOrCreateGridCompartment(10, 1, 2)')) {{
      throw new Error('Expected empty grid coordinate B1 to wire openOrCreateGridCompartment(10, 1, 2)');
    }}

    // 2. Placed compartment A1 (101) supports inline rename & inline quick-add directly from the grid screen
    if (!unitHtml.includes('startGridCellRename(101') || !unitHtml.includes('startGridCellQuickAdd(101')) {{
      throw new Error('Expected placed grid compartment A1 to expose inline rename and quick-add triggers');
    }}

    comp.startGridCellRename(101);
    getEl('grid-cell-rename-101').value = 'Stepper Drivers';
    await comp.saveGridCellRename(101);
    if (stateMod.state.locationMap.get(101).name !== 'Stepper Drivers') {{
      throw new Error('Inline grid rename did not update compartment 101 name');
    }}

    comp.startGridCellQuickAdd(101);
    getEl('grid-cell-add-101').value = '2x XL4015 Buck Module';
    await comp.submitGridCellQuickAdd(101);
    if (!mockItems.some(i => i.location_id === 101 && i.name === 'XL4015 Buck Module')) {{
      throw new Error('Inline grid quick-add did not create item in compartment 101');
    }}

    // 3. Clicking empty compartment B1 creates it & opens its Compartment Page
    await comp.openOrCreateGridCompartment(10, 1, 2);
    if (stateMod.state.currentView !== 'drawer' || stateMod.state.currentLocationId !== 102) {{
      throw new Error(`Expected navigation to new compartment 102 page, got view=${{stateMod.state.currentView}} id=${{stateMod.state.currentLocationId}}`);
    }}

    const compPageHtml = getEl('app-workspace').innerHTML;
    // Compartment page must expose visible rename, direct photo upload, and AI scan of photos
    if (!compPageHtml.includes('toggleDrawerLabelEdit(102, true)')) {{
      throw new Error('Expected visible rename button on compartment page');
    }}
    if (!compPageHtml.includes('triggerDrawerDirectPhotoUpload(102)')) {{
      throw new Error('Expected direct photo upload button wired on compartment page');
    }}
    if (!compPageHtml.includes('scanCompartmentPhotosWithAi(102)')) {{
      throw new Error('Expected AI scan button wired on compartment page when photos exist');
    }}

    // 4. Triggering AI scan on compartment 102 scans its photo and defaults to 'separate' items in compartment 102
    await comp.scanCompartmentPhotosWithAi(102);
    const aiHtml = getEl('rapid-ai-results').innerHTML;
    if (!aiHtml.includes('MP1584EN Step Down')) {{
      throw new Error('Expected AI scan of compartment photo to populate detected items');
    }}
    if (!getEl('rapid-mode-separate').classList.contains('active')) {{
      throw new Error('Expected compartment AI scan to keep separate items mode active by default');
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"Compartment workflow behavioral test failed:\n{proc.stderr}"


def test_unmatched_api_route_returns_404_not_405_and_slot_fallback(client):
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

    # 1. Unmatched /api/* POST routes must return 404 Not Found instead of falling through to StaticFiles (405)
    unknown_post = client.post("/api/v1/nonexistent-endpoint", json={"x": 1})
    assert unknown_post.status_code == 404

    # 2. If a stale backend instance returns 405/404 on /grid/slot, openOrCreateGridCompartment falls back cleanly to POST /api/v1/locations
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_posix = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.BroadcastChannel = undefined;
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.requestAnimationFrame = () => {{}};

    function makeEl() {{
      return {{
        value: '',
        innerHTML: '',
        textContent: '',
        hidden: true,
        style: {{}},
        dataset: {{}},
        classList: {{ add() {{}}, remove() {{}}, toggle() {{}}, contains: () => false }},
        setAttribute() {{}},
        appendChild() {{}},
        remove() {{}},
        focus() {{}},
        select() {{}},
        querySelector: () => null,
        querySelectorAll: () => [],
      }};
    }}
    globalThis.window = {{
      location: {{ host: 'localhost:8000', hash: '#/unit/2', origin: 'http://localhost:8000' }},
      history: {{ pushState(_s, _t, h) {{ globalThis.window.location.hash = h; }} }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
    }};
    Object.defineProperty(globalThis, 'navigator', {{
      value: {{ userAgent: 'Mozilla/5.0 Windows NT 10.0', maxTouchPoints: 0 }},
      configurable: true,
    }});
    globalThis.window.navigator = globalThis.navigator;
    globalThis.history = globalThis.window.history;
    globalThis.document = {{
      getElementById: () => makeEl(),
      createElement: () => makeEl(),
      body: makeEl(),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
      documentElement: makeEl(),
    }};

    const mockTree = [{{
      id: 2,
      name: 'Organizer',
      location_type: 'container',
      grid_rows: 4,
      grid_cols: 4,
      children: [],
    }}];
    let fallbackPostBody = null;
    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      const m = (opts.method || 'GET').toUpperCase();
      if (u === '/api/v1/locations/2/grid/slot' && m === 'POST') {{
        return {{ ok: false, status: 405, json: async () => ({{ detail: 'Method Not Allowed' }}) }};
      }}
      if (u === '/api/v1/locations' && m === 'POST') {{
        fallbackPostBody = JSON.parse(opts.body);
        const created = {{ id: 205, ...fallbackPostBody, children: [], photos: [] }};
        mockTree[0].children.push(created);
        return {{ ok: true, status: 201, json: async () => created }};
      }}
      if (u.includes('/api/v1/locations/tree')) {{
        return {{ ok: true, status: 200, json: async () => mockTree }};
      }}
      if (u.startsWith('/api/v1/items/page')) {{
        return {{ ok: true, status: 200, json: async () => ({{ items: [], total: 0, limit: 200, next_cursor: null, has_more: false }}) }};
      }}
      if (u.startsWith('/api/v1/items?')) {{
        return {{ ok: true, status: 200, json: async () => [] }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const stateMod = await import('file://{js_posix}/state.js');
    const comp = await import('file://{js_posix}/compartment-actions.js');
    await stateMod.loadAllData();
    const res = await comp.openOrCreateGridCompartment(2, 1, 2);
    if (!res || res.id !== 205 || !fallbackPostBody || fallbackPostBody.name !== 'B1' || fallbackPostBody.grid_row !== 1 || fallbackPostBody.grid_col !== 2) {{
      throw new Error(`Expected fallback POST /api/v1/locations for B1, got ${{JSON.stringify(fallbackPostBody)}}`);
    }}
    if (stateMod.state.currentView !== 'drawer' || stateMod.state.currentLocationId !== 205) {{
      throw new Error('Expected fallback to navigate into created compartment 205');
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Slot 405 fallback test failed:\n{proc.stderr}"


def test_grid_view_drag_and_drop_renders_and_calls_move_endpoint():
    import shutil
    import subprocess
    from pathlib import Path

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    const calls = [];
    globalThis.window = {{ location: {{ origin: 'http://localhost' }}, renderCurrentWorkspace: () => {{}}, addEventListener: () => {{}} }};
    globalThis.document = {{
      getElementById: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {{}},
    }};
    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      const m = (opts.method || 'GET').toUpperCase();
      const body = opts.body ? JSON.parse(opts.body) : null;
      calls.push({{ u, m, body }});
      if (u.includes('/grid/move') && m === 'POST') {{
        return {{ ok: true, status: 200, json: async () => ({{ id: 10, children: [] }}) }};
      }}
      if (u.startsWith('/api/v1/locations') || u.startsWith('/api/v1/items')) {{
        return {{ ok: true, status: 200, json: async () => [] }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const gridMod = await import('file://{js_dir}/grid-designer.js');
    const unit = {{
      id: 10,
      name: 'Organizer',
      grid_rows: 1,
      grid_cols: 2,
      grid_config: null,
      children: [
        {{ id: 101, name: 'A1', location_type: 'drawer', grid_row: 1, grid_col: 1, grid_row_span: 1, grid_col_span: 1, children: [] }},
      ],
    }};

    const fullHtml = gridMod.renderContainerGridHtml(unit);
    if (!fullHtml.includes('draggable="true"') || !fullHtml.includes('handleGridSlotDragStart(event, 10, 101)')) {{
      throw new Error('Expected normal grid view to render draggable slot with dragstart handler');
    }}
    if (!fullHtml.includes('handleGridSlotDropOnEmpty(event, 10, 1, 2)')) {{
      throw new Error('Expected normal grid view empty cell to render drop handler');
    }}

    const compactHtml = gridMod.renderContainerGridHtml(unit, {{ compact: true }});
    if (compactHtml.includes('draggable="true"')) {{
      throw new Error('Compact grid view should not render draggable slots');
    }}

    const fakeEvent = {{
      preventDefault: () => {{}},
      dataTransfer: {{
        data: '',
        setData(k, v) {{ this.data = v; }},
        getData() {{ return this.data; }},
      }},
      currentTarget: {{ classList: {{ add() {{}}, remove() {{}} }}, closest: () => null }},
    }};

    gridMod.handleGridSlotDragStart(fakeEvent, 10, 101);
    await gridMod.handleGridSlotDropOnEmpty(fakeEvent, 10, 1, 2);

    const moveCall = calls.find(c => c.u === '/api/v1/locations/10/grid/move' && c.m === 'POST');
    if (!moveCall || moveCall.body.source_id !== 101 || moveCall.body.target_row !== 1 || moveCall.body.target_col !== 2) {{
      throw new Error(`Expected POST /grid/move to (1,2), got ${{JSON.stringify(moveCall)}}`);
    }}

    gridMod.handleGridSlotDragStart(fakeEvent, 10, 101);
    await gridMod.handleGridSlotDropOnSlot(fakeEvent, 10, 102);

    const swapCall = calls.filter(c => c.u === '/api/v1/locations/10/grid/move' && c.m === 'POST')[1];
    if (!swapCall || swapCall.body.source_id !== 101 || swapCall.body.target_id !== 102) {{
      throw new Error(`Expected POST /grid/move swap with 102, got ${{JSON.stringify(swapCall)}}`);
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Grid view drag/drop JS test failed:\n{proc.stderr}"


def test_rapid_scan_scrim_protection_and_collapsed_photo_overflow_badge():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

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
    const unsavedModal = makeEl('unsaved-confirm-modal');
    const workspace = makeEl('app-workspace');
    const elements = {{
      'drawer-rapid-entry-modal': rapidModal,
      'unsaved-confirm-modal': unsavedModal,
      'unsaved-confirm-msg': makeEl('unsaved-confirm-msg'),
      'app-workspace': workspace,
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
      'rapid-existing-photos-btn': makeEl('rapid-existing-photos-btn'),
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
    globalThis.fetch = async (url) => {{
      const u = String(url);
      if (u.includes('/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => ({{ active_provider: 'local', providers: {{ local: {{ configured: true, available: true, model: 'qwen3-vl:8b' }} }} }}) }};
      }}
      if (u.includes('/analyze-photo')) {{
        return await new Promise((res) => {{ resolveScan = res; }});
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const domMod = await import('file://{js_dir}/dom.js');
    const guardMod = await import('file://{js_dir}/unsaved-guard.js');
    const modalsMod = await import('file://{js_dir}/modals.js');
    const viewsMod = await import('file://{js_dir}/views.js');

    const photos = [1, 2, 3, 4, 5, 6].map((id) => ({{
      id,
      photo_path: `/uploads/originals/p${{id}}.jpg`,
      thumbnail_path: `/uploads/thumbnails/p${{id}}_thumb.webp`,
      photo_type: 'container',
    }}));
    stateMod.state.locationMap.set(62, {{
      id: 62,
      name: 'A9',
      location_type: 'drawer',
      grid_row: 1,
      grid_col: 9,
      photos,
      children: [],
    }});
    stateMod.state.items = [];

    // 1. Collapsed bin photos bar with 6 photos should show a +3 overflow badge
    viewsMod.renderDrawerView(62);
    if (!workspace.innerHTML.includes('drawer-photos-preview-more') || !workspace.innerHTML.includes('+3')) {{
      throw new Error('Expected collapsed bin photos preview to show +3 overflow badge when 6 photos exist');
    }}

    // 2. Backdrop click while scanning must NOT close the rapid modal
    await modalsMod.openRapidEntryModal(62, {{ photoIds: [1], autoScan: false }});
    const scanPromise = modalsMod.runRapidAiScan();
    await new Promise(r => setTimeout(r, 20));
    if (rapidModal.dataset.scanning !== 'true') {{
      throw new Error(`Expected rapidModal.dataset.scanning === "true" during scan, got ${{rapidModal.dataset.scanning}}`);
    }}
    domMod.closeOnScrim({{ target: rapidModal }}, 'drawer-rapid-entry-modal');
    if (!rapidModal.classList.contains('open')) {{
      throw new Error('Expected backdrop click during active AI scan to keep rapid modal open');
    }}

    // 3. After AI scan detects items, modal must be marked dirty so backdrop click prompts instead of closing silently
    resolveScan({{
      ok: true,
      status: 200,
      json: async () => ({{
        status: 'success',
        message: 'Found 1 item',
        items: [{{ name: 'AMS1117 3.3V Regulator', estimated_qty: 5 }}],
      }}),
    }});
    await scanPromise;
    if (rapidModal.dataset.scanning !== 'false') {{
      throw new Error(`Expected rapidModal.dataset.scanning === "false" after scan, got ${{rapidModal.dataset.scanning}}`);
    }}
    if (rapidModal.dataset.dirty !== 'true') {{
      throw new Error(`Expected rapidModal.dataset.dirty === "true" after AI detected items, got ${{rapidModal.dataset.dirty}}`);
    }}
    domMod.closeOnScrim({{ target: rapidModal }}, 'drawer-rapid-entry-modal');
    await new Promise(r => setTimeout(r, 10));
    if (!rapidModal.classList.contains('open') || !unsavedModal.classList.contains('open')) {{
      throw new Error('Expected backdrop click after AI results to trigger unsaved confirmation modal instead of discarding items');
    }}
    guardMod.resolveUnsavedPrompt('cancel');
    await new Promise(r => setTimeout(r, 10));
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Rapid scan scrim & overflow test failed:\n{proc.stderr}"


def test_rapid_entry_preserves_ai_crops_and_item_replace_offers_bin_photos(client):
    import shutil
    import subprocess
    from pathlib import Path
    from unittest.mock import patch
    import pytest

    # 1. Backend: include_crops=false preserves ai_crop photos while filling missing photos
    loc = client.post("/api/v1/locations", json={"name": "A9", "location_type": "drawer"}).json()
    crop_item = client.post(
        "/api/v1/items",
        json={
            "name": "Drok Boost Converter",
            "location_id": loc["id"],
            "quantity": 1,
            "item_photo": "/uploads/originals/ai_crop_real.jpg",
        },
    ).json()

    with patch("app.api.locations.find_and_import_images_batch") as mock_batch:
        resp = client.post(f"/api/v1/locations/{loc['id']}/find-item-images?include_crops=false")
        assert resp.status_code == 200, resp.text
        assert resp.json()["checked_count"] == 0
        mock_batch.assert_not_called()

    unchanged = client.get(f"/api/v1/items/{crop_item['id']}").json()
    assert unchanged["item_photo"] == "/uploads/originals/ai_crop_real.jpg"

    # 2. Frontend: submitRapidEntry does not overwrite ai_crop photos & Replace offers bin's 6 uploaded photos
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
      const listeners = {{}};
      return {{
        id,
        value: '',
        textContent: '',
        innerHTML: '',
        style: {{ display: '' }},
        dataset: {{}},
        disabled: false,
        open: false,
        files: [],
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
        remove() {{}},
        addEventListener(ev, fn) {{ (listeners[ev] ||= []).push(fn); }},
        async trigger(ev, eventObj) {{
          for (const fn of (listeners[ev] || [])) await fn(eventObj);
        }},
        querySelector() {{ return null; }},
        scrollIntoView() {{}},
        focus() {{}},
      }};
    }}

    const existingGrid = makeEl('quick-photo-existing-grid');
    const existingWrap = makeEl('quick-photo-existing-wrap');
    const elements = {{
      'drawer-rapid-entry-modal': makeEl('drawer-rapid-entry-modal'),
      'unsaved-confirm-modal': makeEl('unsaved-confirm-modal'),
      'unsaved-confirm-msg': makeEl('unsaved-confirm-msg'),
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
      'rapid-existing-photos-btn': makeEl('rapid-existing-photos-btn'),
      'quick-photo-modal': makeEl('quick-photo-modal'),
      'quick-photo-title': makeEl('quick-photo-title'),
      'quick-photo-input': makeEl('quick-photo-input'),
      'quick-photo-input-label': makeEl('quick-photo-input-label'),
      'quick-photo-preview': makeEl('quick-photo-preview'),
      'quick-photo-icon': makeEl('quick-photo-icon'),
      'quick-photo-dropzone': makeEl('quick-photo-dropzone'),
      'quick-photo-existing-wrap': existingWrap,
      'quick-photo-existing-grid': existingGrid,
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

    const photos = [1, 2, 3, 4, 5, 6].map((id) => ({{
      id,
      photo_path: `/uploads/originals/bin_p${{id}}.jpg`,
      thumbnail_path: `/uploads/thumbnails/bin_p${{id}}_thumb.webp`,
      photo_type: 'container',
    }}));
    const bin62 = {{
      id: 62,
      name: 'A9',
      location_type: 'drawer',
      photos,
      children: [],
    }};
    const item137 = {{ id: 137, name: 'Drok Boost Converter', location_id: 62, item_photo: '/uploads/originals/satellite.jpg' }};

    const apiCalls = [];
    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      const m = (opts.method || 'GET').toUpperCase();
      apiCalls.push({{ u, m, body: opts.body || null }});
      if (u.includes('/vision-providers')) {{
        return {{ ok: true, status: 200, json: async () => ({{ active_provider: 'local', providers: {{ local: {{ configured: true, available: true }} }} }}) }};
      }}
      if (u.includes('/analyze-photo')) {{
        return {{
          ok: true,
          status: 200,
          json: async () => ({{
            status: 'success',
            message: 'Found 1 item',
            items: [{{ name: 'Drok Boost Converter', estimated_qty: 1, crop_photo_path: '/uploads/originals/ai_crop_real.jpg' }}],
          }}),
        }};
      }}
      if (u.endsWith('/api/v1/items/bulk')) {{
        return {{
          ok: true,
          status: 201,
          json: async () => ([{{ id: 137, name: 'Drok Boost Converter', location_id: 62, item_photo: '/uploads/originals/ai_crop_real.jpg' }}]),
        }};
      }}
      if (u.includes('/api/v1/locations/tree')) {{
        return {{ ok: true, status: 200, json: async () => ([bin62]) }};
      }}
      if (u.includes('/api/v1/items/page')) {{
        return {{ ok: true, status: 200, json: async () => ({{ items: [item137], total: 1, limit: 200, next_cursor: null, has_more: false }}) }};
      }}
      if (u.includes('/api/v1/items?')) {{
        return {{ ok: true, status: 200, json: async () => ([item137]) }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{}}) }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const modalsMod = await import('file://{js_dir}/modals.js');

    stateMod.state.locationMap.set(62, bin62);
    stateMod.state.items = [item137];

    // a) Rapid scan save with an ai_crop photo must NOT fire find-item-images
    await modalsMod.openRapidEntryModal(62, {{ photoIds: [1], autoScan: true }});
    await modalsMod.submitRapidEntry();
    const findImageCalls = apiCalls.filter(c => c.u.includes('/find-item-images'));
    if (findImageCalls.length > 0) {{
      throw new Error(`Expected submitRapidEntry NOT to call /find-item-images when item already has ai_crop photo, got ${{JSON.stringify(findImageCalls)}}`);
    }}

    // b) Replace item photo modal for item 137 in bin 62 must list all 6 uploaded bin photos
    modalsMod.openQuickPhotoModal({{ kind: 'item', id: 137, title: 'Replace item photo' }});
    if (existingWrap.style.display !== 'block') {{
      throw new Error(`Expected #quick-photo-existing-wrap to be visible for item with bin photos, got display=${{existingWrap.style.display}}`);
    }}
    for (let i = 1; i <= 6; i++) {{
      if (!existingGrid.innerHTML.includes(`/uploads/originals/bin_p${{i}}.jpg`)) {{
        throw new Error(`Expected #quick-photo-existing-grid to include bin photo ${{i}}, got ${{existingGrid.innerHTML}}`);
      }}
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Crop preservation & item replace test failed:\n{proc.stderr}"


def test_item_view_shows_saved_photos_and_converts_selected_to_single_pdf(client):
    import io
    import shutil
    import subprocess
    from pathlib import Path
    from PIL import Image as PILImage
    import pytest

    loc = client.post("/api/v1/locations", json={"name": "A9", "location_type": "drawer"}).json()
    uploaded_paths = []
    for idx, color in enumerate([(220, 40, 40), (40, 180, 60), (40, 80, 220)], start=1):
        buf = io.BytesIO()
        PILImage.new("RGB", (2200, 1600), color=color).save(buf, format="JPEG", quality=92)
        buf.seek(0)
        up = client.post(
            f"/api/v1/locations/{loc['id']}/photos",
            files={"file": (f"doc_page_{idx}.jpg", buf, "image/jpeg")},
        )
        assert up.status_code == 201, up.text
        uploaded_paths.append(up.json()["photo_path"])

    item = client.post(
        "/api/v1/items",
        json={
            "name": "Drok DC-DC Boost Converter Module",
            "location_id": loc["id"],
            "quantity": 1,
            "item_photo": uploaded_paths[0],
        },
    ).json()

    # Convert selected 2 document images into a single reduced-resolution PDF attachment
    pdf_resp = client.post(
        f"/api/v1/items/{item['id']}/attachments/from-photos-pdf",
        json={
            "photo_paths": [uploaded_paths[1], uploaded_paths[2]],
            "title": "Drok Manual & Specs",
            "max_dimension": 1200,
        },
    )
    assert pdf_resp.status_code == 201, pdf_resp.text
    att = pdf_resp.json()
    assert att["attachment_type"] == "pdf"
    assert att["content_type"] == "application/pdf"
    assert att["filename"].endswith(".pdf")
    assert att["title"] == "Drok Manual & Specs"
    assert att["file_path"].startswith("/uploads/attachments/")
    assert att["size_bytes"] > 100

    dl = client.get(att["file_path"])
    assert dl.status_code == 200
    assert dl.content.startswith(b"%PDF-")
    assert b"/Count 2" in dl.content
    assert dl.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in (dl.headers.get("Content-Security-Policy") or "")

    # Frontend test: renderItemView shows all 6 saved images and converts selected images into one PDF
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
        disabled: false,
        checked: false,
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
        appendChild() {{}},
        remove() {{}},
        addEventListener() {{}},
        querySelector() {{ return null; }},
      }};
    }}

    const workspace = makeEl('app-workspace');
    const pdfTitleInput = makeEl('item-pdf-title-137');
    pdfTitleInput.value = 'Datasheet Pages';
    const pdfBtn = makeEl('item-convert-pdf-btn-137');
    const elements = {{
      'app-workspace': workspace,
      'toast-shelf': makeEl('toast-shelf'),
      'item-pdf-title-137': pdfTitleInput,
      'item-convert-pdf-btn-137': pdfBtn,
      'item-attachments-list-137': makeEl('item-attachments-list-137'),
      'item-audit-history-137': makeEl('item-audit-history-137'),
      'attachment-viewer-modal': makeEl('attachment-viewer-modal'),
      'attachment-viewer-card': makeEl('attachment-viewer-card'),
      'attachment-viewer-badge': makeEl('attachment-viewer-badge'),
      'attachment-viewer-title': makeEl('attachment-viewer-title'),
      'attachment-viewer-meta': makeEl('attachment-viewer-meta'),
      'attachment-viewer-frame': makeEl('attachment-viewer-frame'),
      'attachment-viewer-img-wrap': makeEl('attachment-viewer-img-wrap'),
      'attachment-viewer-img': makeEl('attachment-viewer-img'),
      'attachment-viewer-expand-btn': makeEl('attachment-viewer-expand-btn'),
      'attachment-viewer-newtab': makeEl('attachment-viewer-newtab'),
      'attachment-viewer-download': makeEl('attachment-viewer-download'),
    }};

    globalThis.window = {{
      location: {{ host: 'localhost:8000', origin: 'http://localhost:8000', hash: '' }},
      addEventListener() {{}},
      matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
    }};
    Object.defineProperty(globalThis, 'navigator', {{
      value: {{ userAgent: 'Mozilla/5.0 Windows NT 10.0', maxTouchPoints: 0 }},
      configurable: true,
    }});
    globalThis.window.navigator = globalThis.navigator;
    globalThis.document = {{
      readyState: 'complete',
      activeElement: null,
      getElementById: (id) => elements[id] || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
      createElement: (tag) => makeEl(tag),
    }};

    const apiCalls = [];
    globalThis.fetch = async (url, opts = {{}}) => {{
      const u = String(url);
      const m = (opts.method || 'GET').toUpperCase();
      const body = opts.body ? JSON.parse(opts.body) : null;
      apiCalls.push({{ u, m, body }});
      if (u.endsWith('/attachments/from-photos-pdf') && m === 'POST') {{
        return {{
          ok: true,
          status: 201,
          json: async () => ({{
            id: 55,
            item_id: 137,
            file_path: '/uploads/attachments/merged.pdf',
            filename: 'Datasheet_Pages.pdf',
            attachment_type: 'pdf',
            title: 'Datasheet Pages',
            size_bytes: 42000,
          }}),
        }};
      }}
      if (u.endsWith('/attachments') && m === 'GET') {{
        return {{ ok: true, status: 200, json: async () => ([]) }};
      }}
      if (u.endsWith('/api/v1/items/137') && m === 'GET') {{
        return {{ ok: true, status: 200, json: async () => ({{ id: 137, audit_logs: [] }}) }};
      }}
      return {{ ok: true, status: 200, json: async () => ([]) }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const viewsMod = await import('file://{js_dir}/views.js');

    const photos = [1, 2, 3, 4, 5, 6].map((id) => ({{
      id,
      photo_path: `/uploads/originals/bin_p${{id}}.jpg`,
      thumbnail_path: `/uploads/thumbnails/bin_p${{id}}_thumb.webp`,
      photo_type: 'container',
    }}));
    stateMod.state.locationMap.set(62, {{
      id: 62,
      name: 'A9',
      location_type: 'drawer',
      photos,
      children: [],
    }});
    stateMod.state.items = [{{
      id: 137,
      name: 'Drok DC-DC Boost Converter Module',
      location_id: 62,
      item_photo: '/uploads/originals/bin_p1.jpg',
      quantity: 1,
      unit: 'pcs',
      status: 'stored',
    }}];

    viewsMod.renderItemView(137);
    if (!workspace.innerHTML.includes('item-saved-photos-grid')) {{
      throw new Error('Expected renderItemView to render item-saved-photos-grid');
    }}
    for (let i = 1; i <= 6; i++) {{
      if (!workspace.innerHTML.includes(`/uploads/originals/bin_p${{i}}.jpg`)) {{
        throw new Error(`Expected renderItemView to display saved photo ${{i}}`);
      }}
    }}
    if (workspace.innerHTML.includes('item-pdf-title-137')) {{
      throw new Error('Expected default viewing mode NOT to show PDF conversion form controls until triggered from menu');
    }}
    if (!workspace.innerHTML.includes('toggleItemPhotosMoreMenu(137')) {{
      throw new Error('Expected default viewing mode to include quiet overflow menu trigger for photo actions');
    }}

    // Enter PDF selection mode from the overflow menu action
    viewsMod.toggleItemPdfSelectMode(137, true);
    if (!workspace.innerHTML.includes('item-pdf-title-137')) {{
      throw new Error('Expected PDF selection mode to render PDF toolbar controls');
    }}

    // Select 3 document photos in custom order and convert to 1 PDF
    viewsMod.toggleItemPdfPhotoSelection(137, '/uploads/originals/bin_p4.jpg', true);
    viewsMod.toggleItemPdfPhotoSelection(137, '/uploads/originals/bin_p2.jpg', true);
    viewsMod.toggleItemPdfPhotoSelection(137, '/uploads/originals/bin_p5.jpg', true);
    await viewsMod.convertSelectedItemPhotosToPdf(137);

    const pdfCall = apiCalls.find(c => c.u === '/api/v1/items/137/attachments/from-photos-pdf' && c.m === 'POST');
    if (!pdfCall) {{
      throw new Error('Expected POST /api/v1/items/137/attachments/from-photos-pdf to be called');
    }}
    const expectedOrder = ['/uploads/originals/bin_p4.jpg', '/uploads/originals/bin_p2.jpg', '/uploads/originals/bin_p5.jpg'];
    if (JSON.stringify(pdfCall.body.photo_paths) !== JSON.stringify(expectedOrder)) {{
      throw new Error(`Expected photo_paths in selection order ${{JSON.stringify(expectedOrder)}}, got ${{JSON.stringify(pdfCall.body.photo_paths)}}`);
    }}
    if (workspace.innerHTML.includes('item-pdf-title-137')) {{
      throw new Error('Expected PDF selection mode to exit back to clean viewing mode after creating PDF');
    }}

    // In-app attachment viewer window & fullscreen toggle
    viewsMod.openAttachmentViewer('/uploads/attachments/merged.pdf', 'Datasheet Pages', 'pdf', 'Datasheet_Pages.pdf', '41 KB');
    if (!elements['attachment-viewer-modal'].classList.contains('open')) {{
      throw new Error('Expected openAttachmentViewer to open #attachment-viewer-modal inside the app');
    }}
    if (!String(elements['attachment-viewer-frame'].src || '').startsWith('/uploads/attachments/merged.pdf')) {{
      throw new Error(`Expected #attachment-viewer-frame.src to load PDF, got ${{elements['attachment-viewer-frame'].src}}`);
    }}
    viewsMod.toggleAttachmentViewerFullscreen();
    if (!elements['attachment-viewer-card'].classList.contains('is-fullscreen')) {{
      throw new Error('Expected toggleAttachmentViewerFullscreen to toggle .is-fullscreen on viewer card');
    }}
    viewsMod.closeAttachmentViewerModal();
    if (elements['attachment-viewer-modal'].classList.contains('open')) {{
      throw new Error('Expected closeAttachmentViewerModal to close #attachment-viewer-modal');
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Item saved photos & PDF conversion frontend test failed:\n{proc.stderr}"









def _jpeg_bytes(color=(10, 20, 30)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="JPEG")
    buf.seek(0)
    return buf


def test_pdf_from_photos_remove_source_photos_does_not_crash(client):
    loc = client.post("/api/v1/locations", json={"name": "PDF Room", "location_type": "room"}).json()
    photo = client.post(
        f"/api/v1/locations/{loc['id']}/photos",
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    ).json()
    item = client.post("/api/v1/items", json={"name": "Manual", "location_id": loc["id"]}).json()
    resp = client.post(
        f"/api/v1/items/{item['id']}/attachments/from-photos-pdf",
        json={"photo_paths": [photo["photo_path"]], "remove_source_photos": True},
    )
    assert resp.status_code == 201
    assert client.get(f"/api/v1/locations/{loc['id']}/photos").json() == []


def test_unit_setup_second_unit_creation_rejected(client):
    sess = client.post("/api/v1/unit-setup/sessions", json={}).json()
    url = f"/api/v1/unit-setup/sessions/{sess['token']}/unit"
    body = {"name": "Dresser", "compartment_count": 2}
    assert client.post(url, json=body).status_code == 201
    assert client.post(url, json=body).status_code == 400
    units = [l for l in client.get("/api/v1/locations").json() if l["name"] == "Dresser"]
    assert len(units) == 1


def test_unit_setup_reservation_blocks_concurrent_create(client, db_session):
    from app.models.session import UnitSetupSessionRecord

    sess = client.post("/api/v1/unit-setup/sessions", json={}).json()
    rec = db_session.get(UnitSetupSessionRecord, sess["token"])
    rec.unit_creating = True
    db_session.commit()
    resp = client.post(
        f"/api/v1/unit-setup/sessions/{sess['token']}/unit",
        json={"name": "Racer", "compartment_count": 1},
    )
    assert resp.status_code == 400
    assert not [l for l in client.get("/api/v1/locations").json() if l["name"] == "Racer"]


def test_grid_save_removing_slot_keeps_reparented_children(client):
    unit = client.post("/api/v1/locations", json={"name": "Chest", "location_type": "container"}).json()
    grid = {"grid_rows": 1, "grid_cols": 2, "slots": [
        {"name": "A", "grid_row": 1, "grid_col": 1},
        {"name": "B", "grid_row": 1, "grid_col": 2},
    ]}
    tree = client.put(f"/api/v1/locations/{unit['id']}/grid", json=grid).json()
    a, b = tree["children"][0]["id"], tree["children"][1]["id"]
    sub = client.post("/api/v1/locations", json={"name": "Sub", "location_type": "box", "parent_id": b}).json()
    grid2 = {"grid_rows": 1, "grid_cols": 1, "slots": [{"id": a, "name": "A", "grid_row": 1, "grid_col": 1}]}
    assert client.put(f"/api/v1/locations/{unit['id']}/grid", json=grid2).status_code == 200
    kept = client.get(f"/api/v1/locations/{sub['id']}")
    assert kept.status_code == 200
    assert kept.json()["parent_id"] == unit["id"]


def test_whitespace_only_names_rejected(client):
    assert client.post("/api/v1/items", json={"name": "   "}).status_code == 422
    assert client.post("/api/v1/locations", json={"name": "  ", "location_type": "room"}).status_code == 422


def test_malformed_host_header_port_does_not_crash(client):
    resp = client.get("/api/v1/locations/network-info", headers={"Host": "foo:abc"})
    assert resp.status_code == 200


def test_concurrent_grid_slot_requests_create_single_slot(client):
    unit = client.post("/api/v1/locations", json={"name": "Cab", "location_type": "container"}).json()
    client.put(f"/api/v1/locations/{unit['id']}/grid", json={"grid_rows": 2, "grid_cols": 2, "slots": []})
    first = client.post(f"/api/v1/locations/{unit['id']}/grid/slot", json={"grid_row": 1, "grid_col": 1})
    second = client.post(f"/api/v1/locations/{unit['id']}/grid/slot", json={"grid_row": 1, "grid_col": 1})
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_pending_reference_blocks_photo_cleanup(client, db_session):
    from app.services.photo_service import photo_service
    up = client.post("/api/v1/photos/upload", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}).json()
    path = up["original_path"]
    photo_service.register_pending_reference(db_session, path)
    try:
        assert photo_service.cleanup_unreferenced_photo(db_session, path) is False
    finally:
        photo_service.release_pending_references(db_session)
    assert photo_service.cleanup_unreferenced_photo(db_session, path) is True


def test_shrinking_grid_below_placed_children_rejected(client):
    unit = client.post("/api/v1/locations", json={"name": "Shrinker", "location_type": "container"}).json()
    grid = {"grid_rows": 2, "grid_cols": 2, "slots": [{"name": "D", "grid_row": 2, "grid_col": 2}]}
    assert client.put(f"/api/v1/locations/{unit['id']}/grid", json=grid).status_code == 200
    assert client.patch(f"/api/v1/locations/{unit['id']}", json={"grid_rows": 1}).status_code == 400
    assert client.patch(f"/api/v1/locations/{unit['id']}", json={"grid_rows": 3}).status_code == 200


def test_cmyk_upload_saves_as_jpeg(client):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("CMYK", (40, 40), (0, 0, 0, 0)).save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post("/api/v1/photos/upload", files={"file": ("cmyk.jpg", buf, "image/jpeg")})
    assert resp.status_code == 200


def test_image_download_blocks_host_resolving_to_private_ip(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.services import image_lookup_service
    monkeypatch.setattr(image_lookup_service, "_host_resolves_publicly", lambda host: False)
    http_client = MagicMock()
    http_client.get = AsyncMock()
    saved = asyncio.run(image_lookup_service.download_and_save_image(http_client, "https://evil.example.com/a.png"))
    assert saved is None
    http_client.get.assert_not_called()


def test_sweep_removes_only_old_unreferenced_ai_crops(db_session):
    import os, time
    from app.models.item import Item
    db_session.add(Item(name='anchor'))
    db_session.commit()
    from app.core.config import settings
    from app.services.photo_service import photo_service
    old = settings.ORIGINALS_DIR / "ai_crop_old.jpg"
    fresh = settings.ORIGINALS_DIR / "ai_crop_fresh.jpg"
    for p in (old, fresh):
        p.write_bytes(b"x")
    stale = time.time() - 3 * 24 * 3600
    os.utime(old, (stale, stale))
    assert photo_service.sweep_orphan_ai_crops(db_session) == 1
    assert not old.exists() and fresh.exists()
    fresh.unlink()


def test_oversized_declared_download_is_rejected():
    import asyncio
    import httpx
    from app.services import image_lookup_service as ils
    resp = httpx.Response(200, headers={"content-length": str(ils._MAX_IMAGE_BYTES + 1)})
    try:
        asyncio.run(ils._reject_oversized_response(resp))
        raised = False
    except httpx.HTTPError:
        raised = True
    assert raised


def test_sweep_refuses_to_run_against_empty_database(db_session):
    from app.core.config import settings
    from app.services.photo_service import photo_service
    crop = settings.ORIGINALS_DIR / "ai_crop_guard.jpg"
    crop.write_bytes(b"x")
    import os, time
    stale = time.time() - 3 * 24 * 3600
    os.utime(crop, (stale, stale))
    assert photo_service.sweep_orphan_ai_crops(db_session) == 0
    assert crop.exists()
    crop.unlink()


def test_grid_merge_writes_audit_entry_and_updates_last_seen(client):
    unit = client.post("/api/v1/locations", json={"name": "Merger", "location_type": "container"}).json()
    grid = {"grid_rows": 1, "grid_cols": 2, "slots": [
        {"name": "A", "grid_row": 1, "grid_col": 1},
        {"name": "B", "grid_row": 1, "grid_col": 2},
    ]}
    tree = client.put(f"/api/v1/locations/{unit['id']}/grid", json=grid).json()
    a, b = tree["children"][0]["id"], tree["children"][1]["id"]
    item = client.post("/api/v1/items", json={"name": "Screw", "location_id": b}).json()
    merged = {"grid_rows": 1, "grid_cols": 2, "slots": [
        {"id": a, "name": "A", "grid_row": 1, "grid_col": 1, "grid_col_span": 2, "merge_from_ids": [b]},
    ]}
    assert client.put(f"/api/v1/locations/{unit['id']}/grid", json=merged).status_code == 200
    detail = client.get(f"/api/v1/items/{item['id']}").json()
    assert detail["location_id"] == a
    moves = [log for log in detail["audit_logs"] if log["action"] == "moved"]
    assert moves and moves[0]["location_id"] == a


def test_unit_setup_whitespace_name_rejected(client):
    sess = client.post("/api/v1/unit-setup/sessions", json={}).json()
    resp = client.post(f"/api/v1/unit-setup/sessions/{sess['token']}/unit", json={"name": "   ", "compartment_count": 1})
    assert resp.status_code == 422


def test_reparent_cycle_still_rejected_after_locking(client):
    parent = client.post("/api/v1/locations", json={"name": "P", "location_type": "room"}).json()
    child = client.post("/api/v1/locations", json={"name": "C", "location_type": "container", "parent_id": parent["id"]}).json()
    assert client.patch(f"/api/v1/locations/{parent['id']}", json={"parent_id": child["id"]}).status_code == 400
    other = client.post("/api/v1/locations", json={"name": "Other", "location_type": "room"}).json()
    resp = client.patch(f"/api/v1/locations/{child['id']}", json={"parent_id": other["id"]})
    assert resp.status_code == 200 and resp.json()["parent_id"] == other["id"]


async def test_download_without_content_length_is_capped_while_streaming(monkeypatch):
    from unittest.mock import MagicMock
    from app.services import image_lookup_service as ils

    monkeypatch.setattr(ils, "_MAX_IMAGE_BYTES", 10)
    monkeypatch.setattr(ils.photo_service, "process_and_save", lambda upload: pytest.fail("oversized body must not be saved"))
    big = MagicMock(status_code=200, headers={"content-type": "image/png"}, content=b"x" * 11)

    async def fake_get(url, **kwargs):
        return big

    assert await ils.download_and_save_image(make_http_client(fake_get), "https://upload.wikimedia.org/a.png") is None


def test_location_delete_writes_audit_for_displaced_items(client):
    room = client.post("/api/v1/locations", json={"name": "Doomed", "location_type": "room"}).json()
    item = client.post("/api/v1/items", json={"name": "Orphan Bolt", "location_id": room["id"]}).json()
    before = client.get(f"/api/v1/items/{item['id']}").json()["last_seen_at"]
    assert client.delete(f"/api/v1/locations/{room['id']}").status_code == 204
    after = client.get(f"/api/v1/items/{item['id']}").json()
    assert after["location_id"] is None
    assert any(log["action"] == "moved" and log["location_id"] is None for log in after["audit_logs"])
    assert after["last_seen_at"] >= before


def _old_crop(name):
    import os, time
    from app.core.config import settings
    path = settings.ORIGINALS_DIR / name
    path.write_bytes(b"x")
    stale = time.time() - 3 * 24 * 3600
    os.utime(path, (stale, stale))
    return path


def test_cleanup_unused_crops_defaults_to_dry_run_and_keeps_referenced(client):
    unused = _old_crop("ai_crop_unused_x.jpg")
    used = _old_crop("ai_crop_used_x.jpg")
    client.post("/api/v1/items", json={"name": "Holder", "item_photo": "/uploads/originals/ai_crop_used_x.jpg"})
    try:
        dry = client.post("/api/v1/system/cleanup-unused-crops").json()
        assert dry == {"dry_run": True, "unused_crops": 1}
        assert unused.exists() and used.exists()
        real = client.post("/api/v1/system/cleanup-unused-crops?dry_run=false").json()
        assert real == {"dry_run": False, "unused_crops": 1}
        assert not unused.exists() and used.exists()
    finally:
        unused.unlink(missing_ok=True)
        used.unlink(missing_ok=True)


def test_convert_item_to_box_keeps_photo_reference_registered(client, db_session):
    from app.services.photo_service import photo_service
    up = client.post("/api/v1/photos/upload", files={"file": ("c.jpg", _jpeg_bytes(), "image/jpeg")}).json()
    item = client.post("/api/v1/items", json={"name": "Camera", "item_photo": up["original_path"]}).json()
    resp = client.post(f"/api/v1/items/{item['id']}/convert-to-box", json={"keep_item_inside": False})
    assert resp.status_code == 201
    assert photo_service._inflight_refs.get(up["original_path"], 0) == 0
    assert photo_service.cleanup_unreferenced_photo(db_session, up["original_path"]) is False


def test_unit_quick_search_in_page_highlight_and_grid_zoom():
    import shutil
    import subprocess
    from pathlib import Path

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.requestAnimationFrame = (fn) => fn && fn();
    globalThis.window = {{
      location: {{ origin: 'http://localhost', hash: '#/unit/10', pathname: '/' }},
      innerHeight: 900,
      addEventListener() {{}},
      history: {{ pushState() {{}}, replaceState() {{}} }},
    }};
    globalThis.history = globalThis.window.history;

    function makeEl(id = '') {{
      const classes = new Set();
      return {{
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        style: {{}},
        dataset: {{}},
        scrollWidth: 1200,
        scrollHeight: 1600,
        clientWidth: 900,
        scrolled: null,
        classList: {{
          add: (...c) => c.forEach(x => classes.add(x)),
          remove: (...c) => c.forEach(x => classes.delete(x)),
          toggle: (c, force) => (force === undefined ? (classes.has(c) ? classes.delete(c) : classes.add(c)) : (force ? classes.add(c) : classes.delete(c))),
          contains: (c) => classes.has(c),
        }},
        scrollIntoView(opts) {{ this.scrolled = opts; }},
        getBoundingClientRect() {{ return {{ top: 240 }}; }},
      }};
    }}

    const elements = {{
      'app-workspace': makeEl('app-workspace'),
      'unit-quick-search-results': makeEl('unit-quick-search-results'),
      'unit-quick-search-clear': makeEl('unit-quick-search-clear'),
      'cgrid-wrap-10': makeEl('cgrid-wrap-10'),
      'cgrid-board-10': makeEl('cgrid-board-10'),
      'unit-zoom-fit-label-10': makeEl('unit-zoom-fit-label-10'),
      'drawer-card-102': makeEl('drawer-card-102'),
      'home-search-panel': makeEl('home-search-panel'),
    }};
    elements['drawer-card-102'].classList.add('cgrid-slot');

    globalThis.document = {{
      title: '',
      getElementById: (id) => elements[id] || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const viewsMod = await import('file://{js_dir}/views.js');
    const routerMod = await import('file://{js_dir}/router.js');

    const unit = {{
      id: 10,
      name: 'Yellow storage organizer',
      location_type: 'container',
      parent_id: 1,
      grid_rows: 16,
      grid_cols: 8,
      total_item_count: 2,
      children: [
        {{ id: 101, name: 'RPI 2040 Mini-Zero', label: 'A1', location_type: 'drawer', parent_id: 10, grid_row: 1, grid_col: 1, grid_row_span: 1, grid_col_span: 1, total_item_count: 1, children: [] }},
        {{
          id: 102,
          name: 'ESP32 BATCH A',
          label: 'M2',
          location_type: 'drawer',
          parent_id: 10,
          grid_row: 12,
          grid_col: 4,
          grid_row_span: 1,
          grid_col_span: 1,
          total_item_count: 1,
          children: [
            {{ id: 103, name: 'Sub Box', label: 'SB9', location_type: 'box', parent_id: 102, total_item_count: 1, children: [] }}
          ],
        }},
      ],
    }};
    const room = {{ id: 1, name: 'Office', location_type: 'room', parent_id: null, children: [unit] }};
    stateMod.state.locations = [room];
    stateMod.indexLocations();
    stateMod.state.items = [
      {{ id: 501, name: 'ESP32-S3 DevKit', location_id: 103 }}
    ];

    // 1. Unit quick search finds by label, coordinate, and nested box label
    const byLabel = viewsMod.searchUnitContainerLabels(unit, 'M2');
    if (!byLabel.length || byLabel[0].loc.id !== 102 || byLabel[0].coord !== 'D12') {{
      throw new Error('Expected M2 label match for drawer 102 at D12: ' + JSON.stringify(byLabel));
    }}
    const byNested = viewsMod.searchUnitContainerLabels(unit, 'SB9');
    if (!byNested.length || byNested[0].loc.id !== 103 || byNested[0].targetDrawerId !== 102) {{
      throw new Error('Expected nested box SB9 to resolve targetDrawerId 102: ' + JSON.stringify(byNested));
    }}

    // 2. Render unit view includes quick search input and zoom controls
    stateMod.state.currentView = 'unit';
    stateMod.state.currentLocationId = 10;
    viewsMod.renderStorageUnitView(10);
    const html = elements['app-workspace'].innerHTML;
    if (!html.includes('unit-quick-search-input') || !html.includes('View all') || !html.includes('Width') || !html.includes('50%')) {{
      throw new Error('Unit view missing quick search or zoom controls');
    }}

    // 3. In-page locateDrawerInUnit highlights and scrolls into view without re-rendering
    const beforeHtml = elements['app-workspace'].innerHTML;
    routerMod.locateDrawerInUnit(10, 103);
    if (elements['app-workspace'].innerHTML !== beforeHtml) {{
      throw new Error('locateDrawerInUnit should not re-render when already on unit page');
    }}
    const card = elements['drawer-card-102'];
    if (!card.classList.contains('spotlight-highlight') || !card.classList.contains('is-highlighted')) {{
      throw new Error('Expected drawer-card-102 to be highlighted');
    }}
    if (!card.scrolled || card.scrolled.block !== 'center' || card.scrolled.inline !== 'center') {{
      throw new Error('Expected scrollIntoView centered on both axes: ' + JSON.stringify(card.scrolled));
    }}

    // 4. Zoom out and Fit all apply zoom and is-zoomed-out class
    viewsMod.setUnitGridZoom(10, '0.5');
    if (elements['cgrid-board-10'].style.zoom !== '0.5' || !elements['cgrid-wrap-10'].classList.contains('is-zoomed-out')) {{
      throw new Error('Expected 50% zoom to apply zoom=0.5 and is-zoomed-out');
    }}
    viewsMod.setUnitGridZoom(10, 'fit');
    const fitZoom = parseFloat(elements['cgrid-board-10'].style.zoom);
    if (!(fitZoom > 0.2 && fitZoom < 0.85)) {{
      throw new Error('Expected fit zoom to scale down large 8x16 board, got ' + fitZoom);
    }}

    // 5. Home search includes Locate link for drawer inside unit
    stateMod.state.homeSearchQuery = 'M2';
    stateMod.state.homeSearchResults = [];
    viewsMod.renderHomeSearchPanel();
    const homeHtml = elements['home-search-panel'].innerHTML;
    if (!homeHtml.includes('locateDrawerInUnit(10, 102)')) {{
      throw new Error('Expected home search panel to include locateDrawerInUnit link');
    }}

    // 6. Uniform 2x2 row bands collapse into a single row header, while mixed 2x2 + 1x1 keeps separate rows
    const gridMod = await import('file://{js_dir}/grid-designer.js');
    const uniformBands = gridMod.computeUniformRowBands(2, 4, [
      {{ grid_row: 1, grid_col: 1, grid_row_span: 2, grid_col_span: 2 }},
      {{ grid_row: 1, grid_col: 3, grid_row_span: 2, grid_col_span: 2 }},
    ]);
    if (uniformBands.length !== 1 || uniformBands[0].label !== '1–2') {{
      throw new Error('Expected uniform 2x2 row to collapse into 1–2: ' + JSON.stringify(uniformBands));
    }}
    const mixedBands = gridMod.computeUniformRowBands(2, 4, [
      {{ grid_row: 1, grid_col: 1, grid_row_span: 2, grid_col_span: 2 }},
      {{ grid_row: 1, grid_col: 3, grid_row_span: 1, grid_col_span: 1 }},
      {{ grid_row: 2, grid_col: 3, grid_row_span: 1, grid_col_span: 1 }},
    ]);
    if (mixedBands.length !== 2 || mixedBands[0].label !== '1' || mixedBands[1].label !== '2') {{
      throw new Error('Expected mixed 2x2 + 1x1 rows to stay separate 1 and 2: ' + JSON.stringify(mixedBands));
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Frontend unit quick search / zoom test failed:\n{proc.stderr}"


def test_item_photos_and_location_cards_navigation_and_edge_cases():
    import shutil
    import subprocess
    from pathlib import Path

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.requestAnimationFrame = (fn) => fn && fn();
    globalThis.window = {{
      location: {{ origin: 'http://localhost', hash: '#/item/1', pathname: '/' }},
      innerWidth: 1440,
      innerHeight: 900,
      addEventListener() {{}},
      history: {{ pushState() {{}}, replaceState() {{}} }},
    }};
    globalThis.history = globalThis.window.history;
    globalThis.fetch = async () => ({{ ok: true, status: 200, json: async () => ({{ audit_logs: [], attachments: [] }}) }});

    function makeEl(id = '') {{
      const classes = new Set();
      return {{
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        style: {{}},
        dataset: {{}},
        scrollTop: 0,
        classList: {{
          add: (...c) => c.forEach(x => classes.add(x)),
          remove: (...c) => c.forEach(x => classes.delete(x)),
          contains: (c) => classes.has(c),
        }},
      }};
    }}

    const workspace = makeEl('app-workspace');
    globalThis.document = {{
      title: '',
      getElementById: (id) => (id === 'app-workspace' ? workspace : null),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const viewsMod = await import('file://{js_dir}/views.js');

    const drawerA = {{ id: 101, name: 'Drawer 1', location_type: 'drawer', parent_id: 10, grid_row: 1, grid_col: 1, grid_row_span: 1, grid_col_span: 1, total_item_count: 1, children: [] }};
    const unitA = {{ id: 10, name: 'Day Bed', location_type: 'container', parent_id: 1, container_photo: '/uploads/originals/unit_a.jpg', grid_rows: 1, grid_cols: 3, total_item_count: 1, children: [drawerA] }};
    const roomA = {{ id: 1, name: 'Office', location_type: 'room', parent_id: null, context_photo: '/uploads/originals/room_a.jpg', children: [unitA] }};

    const drawerB = {{ id: 201, name: 'Shelf Top', location_type: 'drawer', parent_id: 20, grid_row: 1, grid_col: 2, grid_row_span: 1, grid_col_span: 1, total_item_count: 1, children: [] }};
    const unitB = {{ id: 20, name: 'Parts Rack', location_type: 'container', parent_id: 2, container_photo: '/uploads/originals/unit_b.jpg', grid_rows: 2, grid_cols: 2, total_item_count: 1, children: [drawerB] }};
    const unitBNoDrawer = {{ id: 21, name: 'Plain Tote', location_type: 'container', parent_id: 2, container_photo: '/uploads/originals/tote.jpg', total_item_count: 1, children: [] }};
    const roomB = {{ id: 2, name: 'Storage Closet', location_type: 'room', parent_id: null, children: [unitB, unitBNoDrawer] }};

    stateMod.state.locations = [roomA, roomB];
    stateMod.indexLocations();

    const item1 = {{ id: 1, name: 'JST Connectors', location_id: 101, item_photo: '/uploads/originals/item1.jpg', attachments: [] }};
    const item2 = {{ id: 2, name: 'M3 Screws', location_id: 201, item_photo: '/uploads/originals/item2.jpg', attachments: [] }};
    const itemNoContainer = {{ id: 3, name: 'Loose Tripod', location_id: 1, item_photo: '/uploads/originals/item3.jpg', attachments: [] }};
    const itemNoSubLoc = {{ id: 4, name: 'Cables', location_id: 21, item_photo: '/uploads/originals/item4.jpg', attachments: [] }};
    stateMod.state.items = [item1, item2, itemNoContainer, itemNoSubLoc];

    function getPhotosLocationGrid(html) {{
      const start = html.indexOf('<div class="item-view-photos-grid"');
      const end = html.indexOf('<div class="item-saved-photos-section');
      if (start === -1 || end === -1 || end <= start) {{
        throw new Error('Could not isolate item-view-photos-grid section');
      }}
      return html.slice(start, end);
    }}

    function getPhotosGallery(html) {{
      const start = html.indexOf('<div class="item-saved-photos-section');
      const end = html.indexOf('<div class="item-where-grid">');
      if (start === -1 || end === -1 || end <= start) {{
        throw new Error('Could not isolate item-saved-photos-section');
      }}
      return html.slice(start, end);
    }}

    // 1. Item 1 in Room 1 -> Unit 10 -> Drawer 101
    viewsMod.renderItemView(1);
    const html1 = workspace.innerHTML;
    const grid1 = getPhotosLocationGrid(html1);
    const gallery1 = getPhotosGallery(html1);
    // Left card stays Item photo with lightbox and Paste/Crop/Replace/Auto-find
    if (!grid1.includes('/uploads/originals/item1.jpg') || !grid1.includes('Item photo')) {{
      throw new Error('Left card must keep Item photo: ' + grid1);
    }}
    for (const tool of ['Paste', 'Crop', 'Replace', 'Auto-find']) {{
      if (!grid1.includes(tool)) {{
        throw new Error(`Left card missing "${{tool}}" button: ` + grid1);
      }}
    }}
    // Middle card shows container photo and navigates to #/unit/10 without lightbox
    if (!grid1.includes('href="#/unit/10"') || !grid1.includes('navToStorageUnit(10, true)') || !grid1.includes('/uploads/originals/unit_a.jpg') || !grid1.includes('Day Bed')) {{
      throw new Error('Middle card missing unit link, photo, or name: ' + grid1);
    }}
    const middleStart1 = grid1.indexOf('href="#/unit/10"');
    const middleEnd1 = grid1.indexOf('</a>', middleStart1);
    const middleAnchor1 = grid1.slice(middleStart1, middleEnd1);
    if (middleAnchor1.includes('openLightbox')) {{
      throw new Error('Middle card must not open lightbox: ' + middleAnchor1);
    }}
    // Right card is purely visual/static
    if (!grid1.includes('is-static-card') || !grid1.includes('Where in Day Bed')) {{
      throw new Error('Right card missing static card or caption: ' + grid1);
    }}
    const rightCardIdx1 = grid1.indexOf('is-static-card');
    const rightCardHtml1 = grid1.slice(rightCardIdx1);
    if (rightCardHtml1.includes('onclick=') || rightCardHtml1.includes('role="button"') || rightCardHtml1.includes('tabindex=') || rightCardHtml1.includes('locateDrawerInUnit') || rightCardHtml1.includes('openLightbox')) {{
      throw new Error('Right card must be purely static without click/button/focus/lightbox attributes: ' + rightCardHtml1);
    }}
    // Photos gallery below stays reverted without extra Auto-find or Replace main photo
    if (gallery1.includes('Auto-find') || gallery1.includes('Replace main photo')) {{
      throw new Error('Photos gallery should stay reverted without extra main-photo actions: ' + gallery1);
    }}

    // 2. Item 2 in Room 2 -> Unit 20 -> Drawer 201 (different container link)
    viewsMod.renderItemView(2);
    const grid2 = getPhotosLocationGrid(workspace.innerHTML);
    if (!grid2.includes('/uploads/originals/item2.jpg') || !grid2.includes('Item photo')) {{
      throw new Error('Item 2 left card should keep Item photo: ' + grid2);
    }}
    if (!grid2.includes('href="#/unit/20"') || !grid2.includes('navToStorageUnit(20, true)') || !grid2.includes('/uploads/originals/unit_b.jpg') || !grid2.includes('Parts Rack')) {{
      throw new Error('Item 2 middle card should link to #/unit/20: ' + grid2);
    }}
    if (!grid2.includes('Where in Parts Rack')) {{
      throw new Error('Item 2 right card missing caption: ' + grid2);
    }}

    // 3. Item with no container -> no broken #/unit/ link
    viewsMod.renderItemView(3);
    const grid3 = getPhotosLocationGrid(workspace.innerHTML);
    if (grid3.includes('#/unit/') || !grid3.includes('No container photo')) {{
      throw new Error('Item with no container should not render a broken #/unit/ link: ' + grid3);
    }}

    // 4. Item with container (id 21) but no location inside -> middle card links to #/unit/21, right card static
    viewsMod.renderItemView(4);
    const grid4 = getPhotosLocationGrid(workspace.innerHTML);
    if (!grid4.includes('href="#/unit/21"') || !grid4.includes('is-static-card')) {{
      throw new Error('Item 4 should link middle card to #/unit/21 and keep right card static: ' + grid4);
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Photos & Location cards test failed:\n{proc.stderr}"


def test_phone_limited_search_container_drawer_item_and_tablet_desktop_views():
    import shutil
    import subprocess
    from pathlib import Path

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    css = (static_dir / "css" / "app.css").read_text(encoding="utf-8")
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    assert "@media (min-width: 768px) and (max-width: 1060px)" in css
    assert "@media (max-width: 767px)" in css
    assert 'id="mobile-notes-modal"' in html

    js_dir = (static_dir / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.requestAnimationFrame = (fn) => fn && fn();

    function makeEl(id = '') {{
      const classes = new Set();
      return {{
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        hidden: false,
        style: {{}},
        dataset: {{}},
        scrollTop: 0,
        focus() {{}},
        scrollIntoView() {{}},
        classList: {{
          add: (...c) => c.forEach(x => classes.add(x)),
          remove: (...c) => c.forEach(x => classes.delete(x)),
          toggle: (c, force) => {{
            if (force === undefined) {{
              if (classes.has(c)) classes.delete(c); else classes.add(c);
            }} else if (force) classes.add(c);
            else classes.delete(c);
          }},
          contains: (c) => classes.has(c),
        }},
      }};
    }}

    const elements = {{
      'app-workspace': makeEl('app-workspace'),
      'mobile-find-input': makeEl('mobile-find-input'),
      'mobile-find-clear-btn': makeEl('mobile-find-clear-btn'),
      'mobile-find-results': makeEl('mobile-find-results'),
      'mobile-notes-modal': makeEl('mobile-notes-modal'),
      'mobile-notes-modal-title': makeEl('mobile-notes-modal-title'),
      'mobile-notes-modal-body': makeEl('mobile-notes-modal-body'),
    }};

    function getElById(id) {{
      if (elements[id]) return elements[id];
      const wsHtml = elements['app-workspace'].innerHTML || '';
      if (wsHtml.includes(`id="${{id}}"`) || wsHtml.includes(`id='${{id}}'`)) {{
        elements[id] = makeEl(id);
        return elements[id];
      }}
      return null;
    }}

    globalThis.document = {{
      title: '',
      documentElement: {{
        getAttribute: () => 'dark',
        setAttribute() {{}},
      }},
      getElementById: getElById,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
    }};

    globalThis.window = {{
      location: {{ origin: 'http://localhost', hash: '#/', pathname: '/' }},
      innerWidth: 900,
      innerHeight: 1100,
      matchMedia: (q) => ({{ matches: q.includes('coarse') }}),
      addEventListener() {{}},
      history: {{ pushState() {{}}, replaceState() {{}} }},
    }};
    globalThis.history = globalThis.window.history;

    const searchQueries = [];
    globalThis.fetch = async (url) => {{
      const u = String(url);
      if (u.includes('/api/v1/search')) {{
        searchQueries.push(u);
        return {{
          ok: true,
          status: 200,
          json: async () => ({{
            query: 'buck',
            total: 1,
            items: [
              {{
                id: 1,
                name: 'Buck Converter',
                location_id: 101,
                quantity: 4,
                unit: 'pcs',
                serial_or_part_number: 'LM2596',
                item_photo: '/uploads/originals/buck.jpg',
                description: 'Input: 4V-40V\\nhttps://example.com/spec',
              }},
            ],
          }}),
        }};
      }}
      if (u.includes('/api/v1/items/1/attachments')) {{
        return {{
          ok: true,
          status: 200,
          json: async () => [
            {{
              id: 50,
              attachment_type: 'image',
              file_path: '/uploads/originals/buck_pinout.jpg',
              thumbnail_path: '/uploads/thumbnails/buck_pinout.webp',
              title: 'Pinout Diagram',
              filename: 'pinout.jpg',
            }},
            {{
              id: 51,
              attachment_type: 'pdf',
              file_path: '/uploads/originals/datasheet.pdf',
              title: 'LM2596 Datasheet',
              filename: 'lm2596.pdf',
              size_bytes: 204800,
            }},
          ],
        }};
      }}
      return {{ ok: true, status: 200, json: async () => ({{ audit_logs: [], attachments: [] }}) }};
    }};

    const stateMod = await import('file://{js_dir}/state.js');
    const viewsMod = await import('file://{js_dir}/views.js');
    const routerMod = await import('file://{js_dir}/router.js');
    const mobileMod = await import('file://{js_dir}/mobile-views.js');

    const boxInside = {{ id: 103, name: 'Resistor Kit', location_type: 'box', label: 'RK1', parent_id: 102, total_item_count: 1, children: [] }};
    const drawerA = {{
      id: 101, name: 'Drawer A', location_type: 'drawer', parent_id: 10,
      grid_row: 1, grid_col: 1, grid_row_span: 1, grid_col_span: 1,
      total_item_count: 1, photos: [{{ id: 7, photo_path: '/uploads/originals/drawera.jpg' }}], children: []
    }};
    const drawerB = {{
      id: 102, name: 'Drawer B', location_type: 'drawer', parent_id: 10,
      grid_row: 1, grid_col: 2, grid_row_span: 1, grid_col_span: 1,
      total_item_count: 2, photos: [{{ id: 8, photo_path: '/uploads/originals/drawerb.jpg' }}], children: [boxInside]
    }};
    const unit = {{
      id: 10, name: 'Parts Cabinet', location_type: 'container', parent_id: 1,
      container_photo: '/uploads/originals/cabinet.jpg', grid_rows: 1, grid_cols: 2,
      total_item_count: 3, children: [drawerA, drawerB]
    }};
    const room = {{ id: 1, name: 'Workshop', location_type: 'room', parent_id: null, children: [unit] }};

    stateMod.state.locations = [room];
    stateMod.indexLocations();
    stateMod.state.items = [
      {{
        id: 1, name: 'Buck Converter', location_id: 101, quantity: 4, unit: 'pcs',
        serial_or_part_number: 'LM2596', item_photo: '/uploads/originals/buck.jpg',
        description: 'Input: 4V-40V\\nhttps://example.com/spec',
      }},
      {{ id: 2, name: 'M3 Screws', location_id: 102, quantity: 50, unit: 'pcs' }},
      {{ id: 3, name: '10k Resistors', location_id: 103, quantity: 100, unit: 'pcs' }},
    ];

    // 1. Tablet (900px, coarse pointer) -> Desktop UI
    if (mobileMod.isPhoneViewport() || mobileMod.shouldUseMobileCompanion() || mobileMod.shouldUseLimitedItemView()) {{
      throw new Error('Tablet 900px should use desktop UI, not phone limited view');
    }}
    viewsMod.renderHomePlacesView();
    if (!elements['app-workspace'].innerHTML.includes('home-canvas-inner')) {{
      throw new Error('Tablet home view should render desktop home-canvas-inner');
    }}
    viewsMod.renderItemView(1);
    if (!elements['app-workspace'].innerHTML.includes('is-item-detail-page')) {{
      throw new Error('Tablet item view should render desktop is-item-detail-page');
    }}

    // 2. Phone (390px) -> Limited Search -> Container -> Drawer -> Item
    globalThis.window.innerWidth = 390;
    if (!mobileMod.isPhoneViewport() || !mobileMod.shouldUseMobileCompanion() || !mobileMod.shouldUseLimitedItemView()) {{
      throw new Error('Phone 390px should enable limited mobile views');
    }}

    // 2a. Search view (empty + active query)
    viewsMod.renderHomePlacesView();
    const emptySearchHtml = elements['app-workspace'].innerHTML + elements['mobile-find-results'].innerHTML;
    if (emptySearchHtml.includes('Add Photos to Drawers')) {{
      throw new Error('Phone search view should not show Add Photos to Drawers banner');
    }}
    mobileMod.handleMobileFindInput({{ target: {{ value: 'buck' }} }});
    await new Promise(r => setTimeout(r, 220));
    if (!searchQueries.some(q => q.includes('/api/v1/search?q=buck'))) {{
      throw new Error('Phone search did not call /api/v1/search: ' + JSON.stringify(searchQueries));
    }}
    const searchResultsHtml = elements['mobile-find-results'].innerHTML;
    for (const expected of ['Buck Converter', 'Workshop', 'Parts cabinet', 'locateDrawerInUnit(10, 101)', 'navToItem(1)']) {{
      if (!searchResultsHtml.includes(expected)) {{
        throw new Error(`Phone search result missing "${{expected}}": ` + searchResultsHtml);
      }}
    }}

    // 2b. Click Storage Container -> Container view with Drawer A highlighted and all drawers clickable
    routerMod.locateDrawerInUnit(10, 101);
    const unitHtml = elements['app-workspace'].innerHTML;
    for (const expected of ['Parts cabinet', 'Workshop', 'Drawer A', 'Here', 'is-highlighted', 'navToDrawer(101, true)', 'navToDrawer(102, true)', 'cabinet-highlight-box']) {{
      if (!unitHtml.includes(expected)) {{
        throw new Error(`Phone container view missing "${{expected}}": ` + unitHtml);
      }}
    }}

    // 2c. Click Drawer B -> Drawer contents view with items & nested boxes
    routerMod.navToDrawer(102, true);
    const drawerHtml = elements['app-workspace'].innerHTML;
    for (const expected of ['Drawer B', 'M3 Screws', 'navToItem(2, true)', 'Resistor Kit', 'navToDrawer(103, true)', '/uploads/originals/drawerb.jpg']) {{
      if (!drawerHtml.includes(expected)) {{
        throw new Error(`Phone drawer view missing "${{expected}}": ` + drawerHtml);
      }}
    }}

    // 2d. Click Item 1 -> Item Detail view (read-only, images, notes + Full View modal, PDF link)
    routerMod.navToItem(1, true);
    await new Promise(r => setTimeout(r, 60));
    const itemHtml = elements['app-workspace'].innerHTML;
    const imagesHtml = elements['mobile-item-images-section-1']?.innerHTML || '';
    const docsHtml = elements['mobile-item-docs-section-1']?.innerHTML || '';
    if (itemHtml.includes('startLimitedItemRename')) {{
      throw new Error('Phone item view should be read-only without Rename action');
    }}
    for (const expected of ['Buck Converter', 'locateDrawerInUnit(10, 101)', 'navToDrawer(101, true)', '4V-40V', 'https://example.com/spec', 'openMobileNotesModal(1)']) {{
      if (!itemHtml.includes(expected)) {{
        throw new Error(`Phone item view missing "${{expected}}": ` + itemHtml);
      }}
    }}
    for (const expected of ['/uploads/originals/buck.jpg', '/uploads/originals/buck_pinout.jpg', 'openLightbox']) {{
      if (!imagesHtml.includes(expected)) {{
        throw new Error(`Phone item images missing "${{expected}}": ` + imagesHtml);
      }}
    }}
    for (const expected of ['LM2596 Datasheet', '/uploads/originals/datasheet.pdf', 'PDF']) {{
      if (!docsHtml.includes(expected)) {{
        throw new Error(`Phone item docs missing "${{expected}}": ` + docsHtml);
      }}
    }}

    mobileMod.openMobileNotesModal(1);
    if (!elements['mobile-notes-modal'].classList.contains('open') || !elements['mobile-notes-modal-body'].innerHTML.includes('4V-40V')) {{
      throw new Error('openMobileNotesModal did not open modal with item notes');
    }}
    mobileMod.closeMobileNotesModal();
    if (elements['mobile-notes-modal'].classList.contains('open')) {{
      throw new Error('closeMobileNotesModal did not close modal');
    }}

    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Phone/tablet limited view test failed:\n{proc.stderr}"


def test_pwa_homescreen_standalone_installability(client):
    import io
    import shutil
    import subprocess
    from pathlib import Path
    from PIL import Image

    manifest_res = client.get("/manifest.webmanifest")
    assert manifest_res.status_code == 200
    assert "application/manifest+json" in manifest_res.headers.get("content-type", "")
    manifest = manifest_res.json()
    assert manifest["id"] == "/"
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert len(manifest["icons"]) >= 3

    for icon_entry in manifest["icons"]:
        icon_res = client.get(icon_entry["src"])
        assert icon_res.status_code == 200
        assert icon_res.headers.get("content-type", "").startswith("image/png")

    apple_res = client.get("/icons/apple-touch-icon.png")
    assert apple_res.status_code == 200
    assert apple_res.headers.get("content-type", "").startswith("image/png")
    apple_img = Image.open(io.BytesIO(apple_res.content))
    assert apple_img.size == (180, 180)
    assert apple_img.mode == "RGB"

    home_res = client.get("/")
    assert home_res.status_code == 200
    home_html = home_res.text
    for expected in (
        'name="mobile-web-app-capable" content="yes"',
        'name="apple-mobile-web-app-capable" content="yes"',
        'name="apple-mobile-web-app-status-bar-style" content="black-translucent"',
        'name="apple-mobile-web-app-title" content="WhereIsMyStuff"',
        'rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png"',
        'rel="manifest" href="/manifest.webmanifest"',
        'id="pwa-install-modal"',
    ):
        assert expected in home_html

    sw_res = client.get("/sw.js")
    assert sw_res.status_code == 200
    assert sw_res.headers.get("cache-control") == "no-cache"
    assert "/icons/apple-touch-icon.png" in sw_res.text

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    function makeEl(id = '') {{
      const classes = new Set();
      return {{
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        style: {{}},
        dataset: {{}},
        classList: {{
          add: (...c) => c.forEach(x => classes.add(x)),
          remove: (...c) => c.forEach(x => classes.delete(x)),
          toggle: (c, f) => (f ? classes.add(c) : classes.delete(c)),
          contains: (c) => classes.has(c),
        }},
      }};
    }}
    const workspace = makeEl('app-workspace');
    const results = makeEl('mobile-find-results');
    globalThis.document = {{
      title: '',
      documentElement: {{ getAttribute: () => 'dark', setAttribute() {{}} }},
      getElementById: (id) => (id === 'app-workspace' ? workspace : (id === 'mobile-find-results' ? results : null)),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
    }};
    let standaloneMedia = false;
    globalThis.window = {{
      location: {{ origin: 'http://localhost', hash: '#/', pathname: '/' }},
      innerWidth: 390,
      innerHeight: 844,
      navigator: {{ standalone: false }},
      matchMedia: (q) => ({{ matches: q.includes('standalone') ? standaloneMedia : false }}),
      addEventListener() {{}},
      history: {{ pushState() {{}}, replaceState() {{}} }},
    }};

    const mobileMod = await import('file://{js_dir}/mobile-views.js');
    mobileMod.renderMobileFindView();
    if (!workspace.innerHTML.includes('id="m-install-app-btn"')) {{
      throw new Error('Expected Install button in browser mode: ' + workspace.innerHTML);
    }}

    globalThis.window.navigator.standalone = true;
    mobileMod.renderMobileFindView();
    if (workspace.innerHTML.includes('id="m-install-app-btn"')) {{
      throw new Error('Expected Install button hidden when navigator.standalone is true');
    }}

    globalThis.window.navigator.standalone = false;
    standaloneMedia = true;
    mobileMod.renderMobileFindView();
    if (workspace.innerHTML.includes('id="m-install-app-btn"')) {{
      throw new Error('Expected Install button hidden when display-mode: standalone matches');
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"PWA standalone UI test failed:\n{proc.stderr}"




