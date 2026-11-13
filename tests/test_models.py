from datetime import datetime, timezone
from app.models.location import Location
from app.models.item import Item
from app.models.audit import AuditLog

def test_location_breadcrumbs_and_path(db_session):
    site = Location(name="Public Storage", location_type="site")
    db_session.add(site)
    db_session.flush()

    unit = Location(name="Unit 42B", location_type="room", parent_id=site.id)
    db_session.add(unit)
    db_session.flush()

    shelf = Location(name="Shelf 3", location_type="shelf", parent_id=unit.id)
    db_session.add(shelf)
    db_session.flush()

    bin_loc = Location(name="Yellow Tote", location_type="container", parent_id=shelf.id)
    db_session.add(bin_loc)
    db_session.commit()

    crumbs = bin_loc.get_breadcrumbs()
    assert len(crumbs) == 4
    assert crumbs[0]["name"] == "Public Storage"
    assert crumbs[1]["name"] == "Unit 42B"
    assert crumbs[2]["name"] == "Shelf 3"
    assert crumbs[3]["name"] == "Yellow Tote"
    assert bin_loc.full_path == "Public Storage > Unit 42B > Shelf 3 > Yellow Tote"

def test_item_and_audit_relationship(db_session):
    loc = Location(name="Workshop", location_type="room")
    db_session.add(loc)
    db_session.flush()

    now = datetime.now(timezone.utc)
    item = Item(
        name="100uF Electrolytic Capacitor",
        category="Electronics",
        tags="capacitor,100uf,radial",
        quantity=25,
        location_id=loc.id,
        last_seen_at=now
    )
    db_session.add(item)
    db_session.flush()

    audit = AuditLog(
        item_id=item.id,
        location_id=loc.id,
        action="created",
        notes="Initial intake"
    )
    db_session.add(audit)
    db_session.commit()

    reloaded = db_session.query(Item).filter(Item.id == item.id).first()
    assert reloaded.name == "100uF Electrolytic Capacitor"
    assert reloaded.location.name == "Workshop"
    assert len(reloaded.audit_logs) == 1
    assert reloaded.audit_logs[0].action == "created"

