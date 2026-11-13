import time

def test_item_lifecycle_photos_and_audit(client):
    # Setup location hierarchy with photos: Shed -> Shelf -> Bin #4
    loc_shed = client.post("/api/v1/locations", json={
        "name": "Storage Shed",
        "location_type": "site",
        "context_photo": "/uploads/originals/shed_overview.jpg"
    }).json()

    loc_bin = client.post("/api/v1/locations", json={
        "name": "Red Tool Tote #4",
        "location_type": "bin",
        "parent_id": loc_shed["id"],
        "container_photo": "/uploads/originals/red_tote_4.jpg"
    }).json()

    # Create Item: DeWalt Reciprocating Saw inside Red Tool Tote #4
    create_resp = client.post("/api/v1/items", json={
        "name": "DeWalt 20V Reciprocating Saw",
        "category": "Power Tools",
        "tags": "saw,dewalt,reciprocating,cordless,cutting",
        "quantity": 1,
        "unit": "tool",
        "serial_or_part_number": "DCS367B",
        "location_id": loc_bin["id"],
        "item_photo": "/uploads/originals/saw.jpg"
    })
    assert create_resp.status_code == 201
    item = create_resp.json()
    item_id = item["id"]

    assert item["name"] == "DeWalt 20V Reciprocating Saw"
    assert item["location_name"] == "Red Tool Tote #4"
    assert item["location_full_path"] == "Storage Shed > Red Tool Tote #4"
    assert item["location_photos"]["container_photo"] == "/uploads/originals/red_tote_4.jpg"
    assert item["location_photos"]["context_photo"] == "/uploads/originals/shed_overview.jpg"
    first_seen = item["last_seen_at"]

    # Verify presence endpoint (User tapped "I see it right here!")
    verify_resp = client.post(f"/api/v1/items/{item_id}/verify", json={
        "notes": "Verified before taking to jobsite"
    })
    assert verify_resp.status_code == 200
    vdata = verify_resp.json()
    assert vdata["last_seen_at"] >= first_seen
    assert len(vdata["audit_logs"]) >= 2
    assert vdata["audit_logs"][0]["action"] == "verified_present"
    assert "jobsite" in vdata["audit_logs"][0]["notes"]

    # Test relocation: Move to Garage Bench
    garage = client.post("/api/v1/locations", json={"name": "Garage Bench", "location_type": "room"}).json()
    patch_resp = client.patch(f"/api/v1/items/{item_id}", json={"location_id": garage["id"]})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["location_name"] == "Garage Bench"

    # Detail view shows move in audit logs
    detail_resp = client.get(f"/api/v1/items/{item_id}")
    assert detail_resp.status_code == 200
    actions = [a["action"] for a in detail_resp.json()["audit_logs"]]
    assert "moved" in actions
    assert "verified_present" in actions
    assert "created" in actions

