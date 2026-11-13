def test_create_and_list_locations(client):
    # Create top level: Shed
    resp = client.post("/api/v1/locations", json={
        "name": "Backyard Shed",
        "location_type": "site",
        "description": "8x10 wooden shed behind garage"
    })
    assert resp.status_code == 201
    shed_id = resp.json()["id"]

    # Create sub-location: North Wall Shelving
    resp = client.post("/api/v1/locations", json={
        "name": "North Wall Shelving",
        "location_type": "shelf",
        "parent_id": shed_id
    })
    assert resp.status_code == 201
    shelf_id = resp.json()["id"]

    # Create storage container with photo references
    resp = client.post("/api/v1/locations", json={
        "name": "Heavy Duty Gray Bin #1",
        "location_type": "bin",
        "parent_id": shelf_id,
        "container_photo": "/uploads/originals/bin1.jpg",
        "context_photo": "/uploads/originals/shelf_north.jpg"
    })
    assert resp.status_code == 201
    bin_data = resp.json()
    assert bin_data["full_path"] == "Backyard Shed > North Wall Shelving > Heavy Duty Gray Bin #1"
    assert bin_data["container_photo"] == "/uploads/originals/bin1.jpg"
    assert bin_data["context_photo"] == "/uploads/originals/shelf_north.jpg"

    # Test list tree view via query param
    tree_resp = client.get("/api/v1/locations?tree=true")
    assert tree_resp.status_code == 200
    tree = tree_resp.json()
    assert len(tree) == 1
    assert tree[0]["name"] == "Backyard Shed"
    assert len(tree[0]["children"]) == 1
    assert tree[0]["children"][0]["name"] == "North Wall Shelving"
    assert len(tree[0]["children"][0]["children"]) == 1
    assert tree[0]["children"][0]["children"][0]["name"] == "Heavy Duty Gray Bin #1"

    # Test direct tree endpoint
    tree_direct = client.get("/api/v1/locations/tree")
    assert tree_direct.status_code == 200
    assert len(tree_direct.json()) == 1

def test_prevent_circular_location_reference(client):
    # A -> B -> C -> try to set A.parent = C
    resp_a = client.post("/api/v1/locations", json={"name": "A", "location_type": "site"})
    a_id = resp_a.json()["id"]

    resp_b = client.post("/api/v1/locations", json={"name": "B", "parent_id": a_id})
    b_id = resp_b.json()["id"]

    resp_c = client.post("/api/v1/locations", json={"name": "C", "parent_id": b_id})
    c_id = resp_c.json()["id"]

    # Attempt to set A's parent to C (descendant)
    resp_bad = client.patch(f"/api/v1/locations/{a_id}", json={"parent_id": c_id})
    assert resp_bad.status_code == 400
    assert "circular" in resp_bad.json()["detail"].lower()

    # Attempt to set A's parent to A
    resp_self = client.patch(f"/api/v1/locations/{a_id}", json={"parent_id": a_id})
    assert resp_self.status_code == 400
    assert "own parent" in resp_self.json()["detail"].lower()


def test_auto_generate_drawers_on_storage_unit_creation(client):
    # Create a Room first
    room_resp = client.post("/api/v1/locations", json={
        "name": "Guest Room",
        "location_type": "room"
    })
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    # Create Storage Unit "Tall Cabinet 1" with auto-generated Drawers 1..10
    unit_resp = client.post("/api/v1/locations", json={
        "name": "Tall Cabinet 1",
        "location_type": "container",
        "parent_id": room_id,
        "auto_generate_children": {
            "prefix": "Drawer",
            "start": 1,
            "end": 10,
            "child_type": "bin"
        }
    })
    assert unit_resp.status_code == 201
    unit_data = unit_resp.json()
    assert unit_data["name"] == "Tall Cabinet 1"
    assert unit_data["child_count"] == 10

    # Verify tree structure has Drawer 1 through Drawer 10
    tree_resp = client.get("/api/v1/locations?tree=true")
    assert tree_resp.status_code == 200
    tree = tree_resp.json()
    guest_room = next(r for r in tree if r["id"] == room_id)
    assert guest_room["child_count"] == 1
    tall_cab = guest_room["children"][0]
    assert len(tall_cab["children"]) == 10
    drawer_names = [d["name"] for d in tall_cab["children"]]
    assert drawer_names[0] == "Drawer 1"
    assert drawer_names[9] == "Drawer 10"

    # Add an item into Drawer 1 and verify total_item_count rolls up to Tall Cabinet 1 and Guest Room
    drawer_1_id = tall_cab["children"][0]["id"]
    item_resp = client.post("/api/v1/items", json={
        "name": "Multimeter Probes",
        "category": "Test Equipment",
        "quantity": 2,
        "location_id": drawer_1_id
    })
    assert item_resp.status_code == 201

    room_updated = client.get(f"/api/v1/locations/{room_id}").json()
    unit_updated = client.get(f"/api/v1/locations/{unit_data['id']}").json()
    drawer_updated = client.get(f"/api/v1/locations/{drawer_1_id}").json()

    assert drawer_updated["item_count"] == 1
    assert drawer_updated["total_item_count"] == 1
    assert unit_updated["item_count"] == 0
    assert unit_updated["total_item_count"] == 1
    assert room_updated["item_count"] == 0
    assert room_updated["total_item_count"] == 1



