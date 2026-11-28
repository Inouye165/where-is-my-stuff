from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
from typing import List

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session

import app.api.system as system_api
from app.core.exceptions import BadRequestError
from app.db.migrations import get_current_schema_version, run_migrations
from app.models.item import Item, ItemAttachment
from app.models.location import Location
from app.services.location_service import LocationService


@contextmanager
def count_selects(bind):
    queries: List[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        head = statement.lstrip().upper()
        if head.startswith("SELECT") or head.startswith("WITH"):
            queries.append(statement)

    event.listen(bind, "before_cursor_execute", before_cursor_execute)
    try:
        yield queries
    finally:
        event.remove(bind, "before_cursor_execute", before_cursor_execute)


def test_migration_010_photo_path_indexes_and_idempotency(tmp_path: Path):
    db_file = tmp_path / "phase3_mig.db"
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        run_migrations(engine)
        run_migrations(engine)
        assert get_current_schema_version(engine) >= 10

        inspector = inspect(engine)
        expected_indexes = {
            "items": {"ix_items_item_photo"},
            "locations": {"ix_locations_container_photo", "ix_locations_context_photo"},
            "location_photos": {"ix_location_photos_photo_path"},
            "item_attachments": {"ix_item_attachments_file_path"},
        }
        for table_name, expected in expected_indexes.items():
            actual = {idx["name"] for idx in inspector.get_indexes(table_name)}
            missing = expected - actual
            assert not missing, f"Missing indexes on {table_name}: {missing}"
    finally:
        engine.dispose()


def test_get_item_detail_avoids_attachment_n_plus_1(client: TestClient, db_session: Session):
    loc = Location(name="Rack 1", location_type="container")
    db_session.add(loc)
    db_session.flush()

    item = Item(name="Multimeter", category="Tools", quantity=1, location_id=loc.id)
    db_session.add(item)
    db_session.flush()

    for idx in range(3):
        db_session.add(
            ItemAttachment(
                item_id=item.id,
                file_path=f"/uploads/attachments/manual_{idx}.pdf",
                filename=f"manual_{idx}.pdf",
                content_type="application/pdf",
                attachment_type="manual",
            )
        )
    db_session.commit()
    item_id = item.id

    with count_selects(db_session.get_bind()) as selects:
        resp = client.get(f"/api/v1/items/{item_id}")

    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["attachments"]) == 3
    assert len(selects) <= 5


def test_validate_no_cycle_uses_single_cte_query(db_session: Session):
    parent_id = None
    nodes: List[Location] = []
    for depth in range(6):
        node = Location(name=f"Level {depth}", parent_id=parent_id, location_type="shelf")
        db_session.add(node)
        db_session.flush()
        nodes.append(node)
        parent_id = node.id
    db_session.commit()

    root_id, leaf_id = nodes[0].id, nodes[-1].id

    with count_selects(db_session.get_bind()) as selects:
        with pytest.raises(BadRequestError):
            LocationService.validate_no_cycle(db_session, root_id, leaf_id)
    assert len(selects) <= 2

    unrelated = Location(name="Other Space", location_type="room")
    db_session.add(unrelated)
    db_session.commit()
    unrelated_id = unrelated.id

    with count_selects(db_session.get_bind()) as selects:
        LocationService.validate_no_cycle(db_session, leaf_id, unrelated_id)
    assert len(selects) <= 2


def test_location_delete_subtree_uses_batched_queries_and_detaches_containers(db_session: Session):
    space = Location(name="Space A", location_type="room")
    db_session.add(space)
    db_session.flush()

    shelf1 = Location(name="Shelf 1", parent_id=space.id, location_type="shelf")
    shelf2 = Location(name="Shelf 2", parent_id=space.id, location_type="shelf")
    db_session.add_all([shelf1, shelf2])
    db_session.flush()

    drawer = Location(name="Drawer A", parent_id=shelf1.id, location_type="drawer")
    db_session.add(drawer)
    db_session.flush()

    cubby = Location(name="Cubby 1", parent_id=drawer.id, location_type="cubby")
    detached_container = Location(name="Bin 1", parent_id=shelf2.id, location_type="container")
    db_session.add_all([cubby, detached_container])
    db_session.flush()

    nested_in_container = Location(name="Tray 1", parent_id=detached_container.id, location_type="drawer")
    db_session.add(nested_in_container)
    db_session.commit()

    space_id = space.id
    shelf1_id = shelf1.id
    drawer_id = drawer.id
    cubby_id = cubby.id
    detached_id = detached_container.id
    nested_id = nested_in_container.id

    with count_selects(db_session.get_bind()) as selects:
        LocationService.delete(db_session, space_id)
        db_session.commit()

    assert len(selects) <= 8

    for deleted_id in (space_id, shelf1_id, drawer_id, cubby_id):
        assert db_session.get(Location, deleted_id) is None

    preserved = db_session.get(Location, detached_id)
    assert preserved is not None
    assert preserved.parent_id is None
    nested = db_session.get(Location, nested_id)
    assert nested is not None
    assert nested.parent_id == detached_id


def test_health_check_caches_positive_probe_but_never_caches_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    system_api._HEALTH_PROBE_CACHE.clear()
    calls = 0
    original_probe = system_api._check_uploads_writable

    def counted_probe() -> bool:
        nonlocal calls
        calls += 1
        return original_probe()

    monkeypatch.setattr(system_api, "_check_uploads_writable", counted_probe)

    resp1 = client.get("/api/v1/health")
    resp2 = client.get("/api/v1/health")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert calls == 1

    monkeypatch.setattr(system_api, "_check_uploads_writable", lambda: False)
    resp_fail = client.get("/api/v1/health")
    assert resp_fail.status_code == 503
    assert resp_fail.json()["status"] == "degraded"


def test_frontend_state_indexing_and_dropdown_deduplication():
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node not installed")

    state_js_url = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "state.js").as_uri()
    script = f"""
import assert from 'node:assert/strict';

function makeElement(tag) {{
  const el = {{
    tagName: tag.toUpperCase(),
    children: [],
    value: '',
    textContent: '',
    _innerHTML: '',
    appendChild(child) {{
      if (child && child._isFragment) {{
        for (const c of child.children) this.children.push(c);
      }} else {{
        this.children.push(child);
      }}
      return child;
    }},
    get options() {{ return this.children; }},
    get innerHTML() {{ return this._innerHTML; }},
    set innerHTML(v) {{
      this._innerHTML = v;
      if (v === '') this.children = [];
    }},
  }};
  return el;
}}

const elements = new Map([
  ['item-location-select', makeElement('select')],
  ['bundle-location-select', makeElement('select')],
  ['rapid-location-select', makeElement('select')],
  ['item-category-datalist', makeElement('datalist')],
  ['rapid-category-datalist', makeElement('datalist')],
]);

globalThis.window = {{ location: {{ origin: 'http://localhost:8000' }} }};
globalThis.document = {{
  getElementById(id) {{ return elements.get(id) || null; }},
  createElement(tag) {{ return makeElement(tag); }},
  createDocumentFragment() {{ return {{ _isFragment: true, children: [], appendChild(c) {{ this.children.push(c); return c; }} }}; }},
}};

const mod = await import({state_js_url!r});
const {{ state, indexLocations, getSubtreeItems, getLocationHierarchy, populateLocationDropdowns }} = mod;

const shelf = {{
  id: 2,
  name: 'Bin 1',
  location_type: 'container',
  parent_id: 1,
  full_path: 'Space A > Bin 1',
  children: [
    {{ id: 3, name: 'Drawer A', location_type: 'drawer', parent_id: 2, full_path: 'Space A > Bin 1 > Drawer A', children: [] }},
  ],
}};
const space = {{
  id: 1,
  name: 'Space A',
  location_type: 'room',
  parent_id: null,
  full_path: 'Space A',
  children: [shelf],
}};

state.locations = [space];
state.categories = ['Hardware', 'Cables'];
state.items = [
  {{ id: 10, name: 'Screw', location_id: 3 }},
  {{ id: 11, name: 'Bolt', location_id: 2 }},
];
indexLocations();

assert.equal(state.containerIdSet.has(2), true);
const hier = getLocationHierarchy(3);
assert.equal(hier.unitName, 'Bin 1');

assert.deepEqual(getSubtreeItems(1).map(i => i.id).sort(), [10, 11]);
state.items = [...state.items, {{ id: 12, name: 'Nut', location_id: 3 }}];
assert.deepEqual(getSubtreeItems(1).map(i => i.id).sort(), [10, 11, 12]);

populateLocationDropdowns();
const itemSel = elements.get('item-location-select');
const firstMarker = itemSel.children[0];
assert.equal(itemSel.children.length, 4);

let innerResetCount = 0;
const origDesc = Object.getOwnPropertyDescriptor(itemSel, 'innerHTML');
Object.defineProperty(itemSel, 'innerHTML', {{
  get() {{ return origDesc.get.call(this); }},
  set(v) {{ innerResetCount += 1; origDesc.set.call(this, v); }},
}});

populateLocationDropdowns();
assert.equal(innerResetCount, 0);
assert.strictEqual(itemSel.children[0], firstMarker);

state.categories = ['Hardware', 'Cables', 'Fasteners'];
populateLocationDropdowns();
assert.equal(elements.get('item-category-datalist').children.length, 3);
assert.equal(innerResetCount, 0);
process.exit(0);
"""
    proc = subprocess.run(  # noqa: S603
        [node_exe, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, f"Node ESM check failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
