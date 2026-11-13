import json
from app.models.location import Location


def test_grid_layout_save_mixed_drawers_and_merge_items(client, db_session):
    # 1. Create a room and a dresser/cabinet container
    room_resp = client.post(
        "/api/v1/locations",
        json={"name": "Workshop", "location_type": "room"},
    )
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    unit_resp = client.post(
        "/api/v1/locations",
        json={"name": "Yellow Organizer", "location_type": "container", "parent_id": room_id},
    )
    assert unit_resp.status_code == 201
    unit_id = unit_resp.json()["id"]

    # 2. Save a 6-col x 3-row grid:
    #    Row 1: 3 drawers (col_span=2 each: A1-B1, C1-D1, E1-F1)
    #    Row 2: 2 larger drawers (col_span=3 each: A2-C2, D2-F2)
    #    Row 3: 2 larger drawers (col_span=3 each: A3-C3, D3-F3)
    grid_payload = {
        "grid_rows": 3,
        "grid_cols": 6,
        "grid_config": json.dumps({"section_breaks": [1]}),
        "slots": [
            {"name": "Top Left", "location_type": "drawer", "grid_row": 1, "grid_col": 1, "grid_row_span": 1, "grid_col_span": 2},
            {"name": "Top Mid", "location_type": "drawer", "grid_row": 1, "grid_col": 3, "grid_row_span": 1, "grid_col_span": 2},
            {"name": "Top Right", "location_type": "drawer", "grid_row": 1, "grid_col": 5, "grid_row_span": 1, "grid_col_span": 2},
            {"name": "Mid Left", "location_type": "drawer", "grid_row": 2, "grid_col": 1, "grid_row_span": 1, "grid_col_span": 3},
            {"name": "Mid Right", "location_type": "drawer", "grid_row": 2, "grid_col": 4, "grid_row_span": 1, "grid_col_span": 3},
            {"name": "Bot Left", "location_type": "drawer", "grid_row": 3, "grid_col": 1, "grid_row_span": 1, "grid_col_span": 3},
            {"name": "Bot Right", "location_type": "drawer", "grid_row": 3, "grid_col": 4, "grid_row_span": 1, "grid_col_span": 3},
        ],
    }
    save_resp = client.put(f"/api/v1/locations/{unit_id}/grid", json=grid_payload)
    assert save_resp.status_code == 200
    tree = save_resp.json()
    assert tree["grid_rows"] == 3
    assert tree["grid_cols"] == 6
    assert json.loads(tree["grid_config"]) == {"section_breaks": [1]}
    assert len(tree["children"]) == 7

    top_left = next(c for c in tree["children"] if c["name"] == "Top Left")
    top_mid = next(c for c in tree["children"] if c["name"] == "Top Mid")
    assert top_left["grid_row"] == 1
    assert top_left["grid_col"] == 1
    assert top_left["grid_col_span"] == 2

    # 3. Put an item in Top Mid, then merge Top Mid into Top Left
    item_resp = client.post(
        "/api/v1/items",
        json={"name": "M3 Screws", "location_id": top_mid["id"], "quantity": 50},
    )
    assert item_resp.status_code == 201
    item_id = item_resp.json()["id"]

    merge_payload = {
        "grid_rows": 3,
        "grid_cols": 6,
        "grid_config": json.dumps({"section_breaks": [1]}),
        "slots": [
            {
                "id": top_left["id"],
                "name": "Top Wide",
                "location_type": "drawer",
                "grid_row": 1,
                "grid_col": 1,
                "grid_row_span": 1,
                "grid_col_span": 4,
                "merge_from_ids": [top_mid["id"]],
            },
            {"name": "Top Right", "location_type": "drawer", "grid_row": 1, "grid_col": 5, "grid_row_span": 1, "grid_col_span": 2},
            {"name": "Mid Left", "location_type": "drawer", "grid_row": 2, "grid_col": 1, "grid_row_span": 1, "grid_col_span": 3},
            {"name": "Mid Right", "location_type": "drawer", "grid_row": 2, "grid_col": 4, "grid_row_span": 1, "grid_col_span": 3},
            {"name": "Bot Left", "location_type": "drawer", "grid_row": 3, "grid_col": 1, "grid_row_span": 1, "grid_col_span": 3},
            {"name": "Bot Right", "location_type": "drawer", "grid_row": 3, "grid_col": 4, "grid_row_span": 1, "grid_col_span": 3},
        ],
    }
    merge_resp = client.put(f"/api/v1/locations/{unit_id}/grid", json=merge_payload)
    assert merge_resp.status_code == 200
    merged_tree = merge_resp.json()
    assert len(merged_tree["children"]) == 6

    # Verify item was moved to Top Wide (top_left["id"]) and not lost
    item_check = client.get(f"/api/v1/items/{item_id}")
    assert item_check.status_code == 200
    assert item_check.json()["location_id"] == top_left["id"]


def test_grid_layout_rejects_overlap_and_out_of_bounds(client):
    unit_resp = client.post(
        "/api/v1/locations",
        json={"name": "Cabinet", "location_type": "container"},
    )
    unit_id = unit_resp.json()["id"]

    # Out of bounds
    oob_resp = client.put(
        f"/api/v1/locations/{unit_id}/grid",
        json={
            "grid_rows": 2,
            "grid_cols": 2,
            "slots": [
                {"name": "Too Wide", "grid_row": 1, "grid_col": 2, "grid_row_span": 1, "grid_col_span": 2}
            ],
        },
    )
    assert oob_resp.status_code == 400

    # Overlapping slots
    overlap_resp = client.put(
        f"/api/v1/locations/{unit_id}/grid",
        json={
            "grid_rows": 2,
            "grid_cols": 2,
            "slots": [
                {"name": "A", "grid_row": 1, "grid_col": 1, "grid_row_span": 1, "grid_col_span": 2},
                {"name": "B", "grid_row": 1, "grid_col": 2, "grid_row_span": 1, "grid_col_span": 1},
            ],
        },
    )
    assert overlap_resp.status_code == 400

