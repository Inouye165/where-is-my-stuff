import io
from sqlalchemy import event
from PIL import Image
from app.core.config import settings


def test_health_endpoint_and_security_headers(client):
    resp = client.get("/api/v1/health", headers={"X-Request-ID": "audit-req-001"})
    assert resp.status_code == 200

    # Security & correlation headers
    assert resp.headers.get("X-Request-ID") == "audit-req-001"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "strict-origin" in resp.headers.get("Referrer-Policy", "")

    body = resp.json()
    assert body["status"] == "healthy"
    assert body["database"] == "ok"
    assert body["uploads_writable"] is True
    assert body["schema_version"] >= 2


def test_versioned_migrations_are_idempotent(db_session):
    from app.db.migrations import run_migrations, get_current_schema_version

    engine = db_session.get_bind()
    v1 = run_migrations(engine)
    v2 = run_migrations(engine)
    assert v1 == v2
    assert v1 >= 2
    assert get_current_schema_version(engine) == v1


def test_location_tree_and_item_list_eliminate_n_plus_1_queries(client, db_session):
    # Create 3 rooms -> 6 containers -> 18 drawers -> 36 items
    for r_idx in range(3):
        room = client.post("/api/v1/locations", json={"name": f"Room {r_idx}", "location_type": "room"}).json()
        for c_idx in range(2):
            unit = client.post("/api/v1/locations", json={
                "name": f"Container {r_idx}-{c_idx}",
                "location_type": "container",
                "parent_id": room["id"],
                "auto_generate_children": {"prefix": "Drawer", "start": 1, "end": 3, "child_type": "bin"}
            }).json()
            tree = client.get("/api/v1/locations/tree").json()
            room_node = next(n for n in tree if n["id"] == room["id"])
            unit_node = next(u for u in room_node["children"] if u["id"] == unit["id"])
            for d in unit_node["children"]:
                for i_idx in range(2):
                    client.post("/api/v1/items", json={
                        "name": f"Part {r_idx}-{c_idx}-{d['id']}-{i_idx}",
                        "location_id": d["id"],
                        "quantity": 3
                    })

    engine = db_session.get_bind()
    statements = []

    def count_stmt(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_stmt)
    try:
        statements.clear()
        tree_resp = client.get("/api/v1/locations/tree")
        assert tree_resp.status_code == 200
        assert len(statements) <= 4, f"Expected <=4 SELECTs for tree, got {len(statements)}"

        statements.clear()
        items_resp = client.get("/api/v1/items?limit=100")
        assert items_resp.status_code == 200
        assert len(items_resp.json()) == 36
        assert len(statements) <= 4, f"Expected <=4 SELECTs for items list, got {len(statements)}"
    finally:
        event.remove(engine, "before_cursor_execute", count_stmt)


def test_xss_sqli_path_traversal_and_sort_whitelist(client):
    loc = client.post("/api/v1/locations", json={
        "name": "Shelf <script>alert('room')</script>",
        "location_type": "room"
    }).json()

    # Path traversal or javascript URI in photo field must be rejected with 422
    bad_photo_resp = client.post("/api/v1/items", json={
        "name": "Bad Photo Item",
        "item_photo": "../../etc/passwd"
    })
    assert bad_photo_resp.status_code == 422

    js_photo_resp = client.post("/api/v1/items", json={
        "name": "JS URI Item",
        "item_photo": "javascript:alert(1)"
    })
    assert js_photo_resp.status_code == 422

    # XSS strings in text fields are stored verbatim on backend (escaped at DOM boundary)
    xss_item = client.post("/api/v1/items", json={
        "name": "<img src=x onerror=alert(1)> Wire < 12AWG",
        "description": "5\" Pliers & 'Cutters' 100%_pure",
        "location_id": loc["id"],
        "quantity": 2
    })
    assert xss_item.status_code == 201
    created = xss_item.json()
    assert created["name"] == "<img src=x onerror=alert(1)> Wire < 12AWG"

    # SQL injection & literal '%'/'_' wildcard safety in search
    sqli_search = client.get("/api/v1/search", params={"q": "100%_pure"})
    assert sqli_search.status_code == 200
    assert sqli_search.json()["total"] == 1

    drop_search = client.get("/api/v1/search", params={"q": "'; DROP TABLE items; --"})
    assert drop_search.status_code == 200

    # Invalid sort parameter rejected cleanly (whitelist enforced)
    bad_sort = client.get("/api/v1/items", params={"sort": "id; DROP TABLE items"})
    assert bad_sort.status_code == 422

    good_sort = client.get("/api/v1/items", params={"sort": "name_asc"})
    assert good_sort.status_code == 200
    assert len(good_sort.json()) >= 1


def test_orphan_photo_cleanup_on_item_and_location_delete(client):
    def upload_sample_jpeg() -> dict:
        img = Image.new("RGB", (320, 240), color=(90, 140, 190))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        r = client.post("/api/v1/photos/upload", files={"file": ("sample.jpg", buf, "image/jpeg")})
        assert r.status_code == 200
        return r.json()

    p1 = upload_sample_jpeg()
    p2 = upload_sample_jpeg()

    orig1 = settings.ORIGINALS_DIR / p1["filename"]
    thumb1 = settings.THUMBNAILS_DIR / (p1["filename"].rsplit(".", 1)[0] + "_thumb.webp")
    orig2 = settings.ORIGINALS_DIR / p2["filename"]

    assert orig1.exists() and thumb1.exists() and orig2.exists()

    item = client.post("/api/v1/items", json={
        "name": "Cordless Drill",
        "item_photo": p1["original_path"]
    }).json()

    # Replacing item_photo cleans up p1 automatically since nothing else references it
    upd = client.patch(f"/api/v1/items/{item['id']}", json={"item_photo": p2["original_path"]})
    assert upd.status_code == 200
    assert not orig1.exists() and not thumb1.exists()
    assert orig2.exists()

    # Deleting the item cleans up p2 automatically
    del_resp = client.delete(f"/api/v1/items/{item['id']}")
    assert del_resp.status_code == 204
    assert not orig2.exists()

    # Subtree delete cleans child location photos while preserving shared photos
    p_room = upload_sample_jpeg()
    p_child = upload_sample_jpeg()
    p_shared = upload_sample_jpeg()

    room = client.post("/api/v1/locations", json={
        "name": "Temp Garage",
        "location_type": "room",
        "context_photo": p_room["original_path"]
    }).json()
    child = client.post("/api/v1/locations", json={
        "name": "Temp Shelf",
        "location_type": "bin",
        "parent_id": room["id"],
        "container_photo": p_child["original_path"],
        "context_photo": p_shared["original_path"]
    }).json()
    assert child["parent_id"] == room["id"]

    # Another item also uses p_shared -> deleting room must NOT delete p_shared
    keeper = client.post("/api/v1/items", json={
        "name": "Keeper Item",
        "item_photo": p_shared["original_path"]
    }).json()
    assert keeper["id"] > 0

    del_room = client.delete(f"/api/v1/locations/{room['id']}")
    assert del_room.status_code == 204
    assert not (settings.ORIGINALS_DIR / p_room["filename"]).exists()
    assert not (settings.ORIGINALS_DIR / p_child["filename"]).exists()
    assert (settings.ORIGINALS_DIR / p_shared["filename"]).exists()


def test_structured_error_envelopes_and_backup_endpoint(client, tmp_path, monkeypatch):
    import sqlite3

    # 404 structured envelope without stack trace leaks
    r404 = client.get("/api/v1/items/999999", headers={"X-Request-ID": "err-check-404"})
    assert r404.status_code == 404
    body404 = r404.json()
    assert "detail" in body404
    assert body404["error"]["code"] == "not_found"
    assert body404["error"]["request_id"] == "err-check-404"
    assert "Traceback" not in r404.text

    # 422 structured envelope
    r422 = client.post("/api/v1/items", json={"name": "", "quantity": -5})
    assert r422.status_code == 422
    assert r422.json()["error"]["code"] == "validation_error"

    # Hot backup endpoint + retention pruning
    sample_db = tmp_path / "api_backup_src.db"
    conn = sqlite3.connect(str(sample_db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    bdir = tmp_path / "api_backups"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{sample_db.as_posix()}")
    monkeypatch.setattr(settings, "BACKUP_DIR", bdir)
    monkeypatch.setattr(settings, "BACKUP_RETENTION_COUNT", 2)

    for _ in range(3):
        b_resp = client.post("/api/v1/system/backup")
        assert b_resp.status_code == 201
        assert b_resp.json()["integrity_ok"] is True

    kept = list(bdir.glob("inventory_backup_*.db"))
    assert len(kept) == 2


def test_no_unescaped_photo_src_interpolations_in_frontend_modules():
    from pathlib import Path
    import re

    js_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    forbidden_raw_src = re.compile(r'src="\$\{(?!escapeHtml\()[^}]+\}"')
    for js_file in js_dir.glob("*.js"):
        content = js_file.read_text(encoding="utf-8")
        matches = forbidden_raw_src.findall(content)
        assert not matches, f"Unescaped dynamic src attribute in {js_file.name}: {matches}"


def test_cors_blocks_untrusted_origins_by_default(client):
    resp = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower(): v for k, v in resp.headers.items()}


def test_empty_state_has_no_fake_starter_rooms():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    state_js = (root / "state.js").read_text(encoding="utf-8")
    views_js = (root / "views.js").read_text(encoding="utf-8")
    assert "No rooms added yet" in views_js
    assert "No items added yet" in views_js
    assert "for (const starter of getStarterSuggestions())" not in views_js
    assert "return [];" in state_js

