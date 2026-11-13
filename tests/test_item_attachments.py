from io import BytesIO
from PIL import Image


def _make_png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (100, 80), color=(40, 120, 220)).save(buf, format="PNG")
    return buf.getvalue()


def test_item_attachments_pdf_and_chart_lifecycle(client):
    loc = client.post("/api/v1/locations", json={"name": "H1", "location_type": "drawer"}).json()
    item = client.post(
        "/api/v1/items",
        json={
            "name": "MT3608 Boost Converter",
            "category": "Electronics",
            "quantity": 3,
            "location_id": loc["id"],
            "description": "DC-DC Step-Up Boost Converter 2V-24V to 5V-28V 2A",
        },
    ).json()

    # Upload a PDF datasheet
    pdf_res = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("MT3608-Datasheet.pdf", b"%PDF-1.4\n%test\n", "application/pdf")},
        data={"title": "MT3608 Datasheet"},
    )
    assert pdf_res.status_code == 201, pdf_res.text
    pdf_data = pdf_res.json()
    assert pdf_data["filename"] == "MT3608-Datasheet.pdf"
    assert pdf_data["attachment_type"] == "pdf"
    assert pdf_data["file_path"].startswith("/uploads/attachments/")

    # Upload a PNG pinout chart
    png_res = client.post(
        f"/api/v1/items/{item['id']}/attachments",
        files={"file": ("pinout-chart.png", _make_png_bytes(), "image/png")},
        data={"title": "Pinout Diagram"},
    )
    assert png_res.status_code == 201, png_res.text
    png_data = png_res.json()
    assert png_data["attachment_type"] == "image"
    assert png_data["thumbnail_path"] is not None

    # List attachments
    list_res = client.get(f"/api/v1/items/{item['id']}/attachments")
    assert list_res.status_code == 200
    assert len(list_res.json()) == 2

    # Item detail includes attachments
    detail_res = client.get(f"/api/v1/items/{item['id']}")
    assert detail_res.status_code == 200
    assert len(detail_res.json()["attachments"]) == 2

    # Delete PDF attachment
    del_res = client.delete(f"/api/v1/items/{item['id']}/attachments/{pdf_data['id']}")
    assert del_res.status_code == 204
    assert len(client.get(f"/api/v1/items/{item['id']}/attachments").json()) == 1

