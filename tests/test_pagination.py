from datetime import datetime, timedelta, timezone


def _seed_items(client, count=8):
    loc = client.post("/api/v1/locations", json={"name": "Pagination Bin", "location_type": "drawer"}).json()
    other_loc = client.post("/api/v1/locations", json={"name": "Other Bin", "location_type": "drawer"}).json()
    created = []
    for idx in range(count):
        item = client.post(
            "/api/v1/items",
            json={
                "name": f"Part {chr(72 - idx)}" if idx < 5 else "Part SameName",
                "category": "Resistors" if idx % 2 == 0 else "Capacitors",
                "quantity": (idx % 3) + 1,
                "location_id": loc["id"] if idx < count - 1 else other_loc["id"],
                "status": "in_use" if idx == 0 else "stored",
            },
        ).json()
        created.append(item)
    return loc, other_loc, created


def test_cursor_pagination_traverses_all_items_without_duplicates_or_gaps(client, db_session):
    from app.models.item import Item

    _, _, created = _seed_items(client, count=8)
    # Force identical timestamps on two items to verify deterministic ID tie-breaking
    same_ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    for row in db_session.query(Item).filter(Item.id.in_([created[2]["id"], created[3]["id"]])).all():
        row.last_seen_at = same_ts
        row.updated_at = same_ts
    db_session.commit()

    for sort in (
        "last_seen_desc",
        "last_seen_asc",
        "name_asc",
        "name_desc",
        "qty_desc",
        "updated_desc",
    ):
        collected_ids = []
        cursor = None
        pages = 0
        while True:
            params = {"sort": sort, "limit": 3}
            if cursor:
                params["cursor"] = cursor
            res = client.get("/api/v1/items/page", params=params)
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["total"] == 8
            assert body["limit"] == 3
            collected_ids.extend(it["id"] for it in body["items"])
            pages += 1
            if not body["has_more"]:
                assert body["next_cursor"] is None
                break
            assert body["next_cursor"]
            cursor = body["next_cursor"]
            assert pages < 10

        assert len(collected_ids) == 8, f"Sort {sort} returned {len(collected_ids)} items"
        assert len(set(collected_ids)) == 8, f"Sort {sort} returned duplicate IDs: {collected_ids}"


def test_cursor_pagination_with_filters_and_invalid_cursor_errors(client):
    loc, _, _ = _seed_items(client, count=7)

    # Filter by location + category
    p1 = client.get("/api/v1/items/page", params={"location_id": loc["id"], "category": "Resistors", "limit": 2})
    assert p1.status_code == 200
    b1 = p1.json()
    assert b1["total"] == 3
    assert len(b1["items"]) == 2
    assert b1["has_more"] is True

    p2 = client.get(
        "/api/v1/items/page",
        params={"location_id": loc["id"], "category": "Resistors", "limit": 2, "cursor": b1["next_cursor"]},
    )
    assert p2.status_code == 200
    b2 = p2.json()
    assert b2["total"] == 3
    assert len(b2["items"]) == 1
    assert b2["has_more"] is False
    assert b2["next_cursor"] is None

    # Corrupted cursor -> 400
    bad_cur = client.get("/api/v1/items/page", params={"cursor": "not-valid-base64!!!"})
    assert bad_cur.status_code == 400

    # Sort mismatch cursor -> 400
    mismatch = client.get("/api/v1/items/page", params={"sort": "name_asc", "cursor": b1["next_cursor"]})
    assert mismatch.status_code == 400


def test_pagination_bounds_and_legacy_offset(client):
    _seed_items(client, count=6)

    # Enforced caps
    assert client.get("/api/v1/items/page?limit=201").status_code == 422
    assert client.get("/api/v1/items?limit=501").status_code == 422

    # Legacy endpoint flat list with offset
    first = client.get("/api/v1/items", params={"sort": "name_asc", "limit": 3, "offset": 0}).json()
    second = client.get("/api/v1/items", params={"sort": "name_asc", "limit": 3, "offset": 3}).json()
    assert isinstance(first, list) and len(first) == 3
    assert isinstance(second, list) and len(second) == 3
    assert {it["id"] for it in first}.isdisjoint({it["id"] for it in second})
