def test_search_scenarios(client):
    # Setup test locations
    house = client.post("/api/v1/locations", json={"name": "Main House", "location_type": "site"}).json()
    pantry = client.post("/api/v1/locations", json={"name": "Kitchen Pantry", "parent_id": house["id"], "location_type": "room"}).json()
    shed = client.post("/api/v1/locations", json={"name": "Backyard Shed", "location_type": "site"}).json()
    storage = client.post("/api/v1/locations", json={"name": "Public Storage Unit 14", "location_type": "site"}).json()

    # Create 4 specific items requested in vision:
    # 1. Capacitor
    client.post("/api/v1/items", json={
        "name": "100uF 50V Radial Aluminum Capacitor",
        "category": "Electronics",
        "tags": "capacitor,100uf,radial,components",
        "quantity": 50,
        "location_id": shed["id"]
    })

    # 2. Saw
    client.post("/api/v1/items", json={
        "name": "Reciprocating Saw Blades 12-inch Wood/Metal",
        "category": "Tools",
        "tags": "saw,blades,dewalt,reciprocating",
        "quantity": 5,
        "location_id": shed["id"]
    })

    # 3. Portable vacuum
    client.post("/api/v1/items", json={
        "name": "Makita 18V Cordless Portable Vacuum",
        "category": "Appliances",
        "tags": "vacuum,portable,handheld,makita,cleaning",
        "quantity": 1,
        "location_id": pantry["id"]
    })

    # 4. Dog medicine
    client.post("/api/v1/items", json={
        "name": "NexGard Plus Dog Chewables",
        "category": "Pet Care",
        "tags": "dog,medicine,flea,tick,heartworm",
        "quantity": 6,
        "location_id": pantry["id"]
    })

    # Test query: "capacitor"
    res1 = client.get("/api/v1/search?q=capacitor").json()
    assert res1["total"] == 1
    assert "100uF" in res1["items"][0]["name"]

    # Test query: "saw"
    res2 = client.get("/api/v1/search?q=saw").json()
    assert res2["total"] == 1
    assert "Reciprocating Saw" in res2["items"][0]["name"]

    # Test query: "vacuum"
    res3 = client.get("/api/v1/search?q=vacuum").json()
    assert res3["total"] == 1
    assert "Makita" in res3["items"][0]["name"]

    # Test query: "dog medicine"
    res4 = client.get("/api/v1/search?q=dog medicine").json()
    assert res4["total"] == 1
    assert "NexGard" in res4["items"][0]["name"]

    # Test search by Location name: "Shed" should return both the capacitor and the saw
    res_shed = client.get("/api/v1/search?q=Shed").json()
    names = [it["name"] for it in res_shed["items"]]
    assert any("Capacitor" in n for n in names)
    assert any("Saw" in n for n in names)


def test_plain_language_and_photo_search(client):
    import io
    from PIL import Image

    garage = client.post("/api/v1/locations", json={"name": "Garage", "location_type": "room"}).json()
    client.post("/api/v1/items", json={
        "name": "Utility Knife",
        "description": "Retractable box cutter for cardboard and packaging",
        "category": "Tools",
        "tags": "cutter,blade,cardboard",
        "location_id": garage["id"],
    })
    client.post("/api/v1/items", json={
        "name": "USB-C Charger",
        "description": "65W fast wall adapter",
        "category": "Electronics",
        "location_id": garage["id"],
    })

    # Plain-language query ignores stop words ("something", "to") so it does not match unrelated items
    res = client.get("/api/v1/search?q=something to cut cardboard").json()
    names = [it["name"] for it in res["items"]]
    assert "Utility Knife" in names
    assert "USB-C Charger" not in names

    # Photo search endpoint accepts an image and returns SearchResult-shaped response
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(120, 140, 160)).save(buf, format="JPEG")
    buf.seek(0)
    photo_res = client.post(
        "/api/v1/search/photo",
        files={"file": ("utility_knife.jpg", buf, "image/jpeg")},
    )
    assert photo_res.status_code == 200
    payload = photo_res.json()
    assert "items" in payload
    assert "query" in payload
    assert any(it["name"] == "Utility Knife" for it in payload["items"])


