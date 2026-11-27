import io
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from app.services import item_bundle_service as bundle_mod


def _make_pdf_bytes(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_png_bytes(color=(30, 140, 220)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_analyze_bundle_combines_pdf_images_links_and_text(client, monkeypatch):
    loc = client.post("/api/v1/locations", json={"name": "B15", "location_type": "bin"}).json()

    captured = {}

    async def fake_fetch_url(url: str):
        captured.setdefault("fetched_urls", []).append(url)
        return (
            "Adafruit IR Break Beam Sensor 3mm LEDs — Product Page. "
            "Works up to 25cm, 3.3V-5V DC, includes 1 emitter and 1 receiver.",
            None,
        )

    async def fake_call_ai(prompt: str, image_paths, *, provider=None, model=None):
        captured["prompt"] = prompt
        captured["image_count"] = len(image_paths)
        return (
            {
                "name": "IR Break-Beam Sensor (3mm LEDs)",
                "part_number": "ADA-2167",
                "category": "Sensors",
                "quantity": 3,
                "unit": "pair",
                "tags": "ir, infrared, break-beam, sensor, 3mm, optical",
                "specs": "Supply: 3.3V-5.5V DC; Sensing range: up to 25cm; Output: NPN open-collector, 100mA max.",
                "use_case": "Detecting objects passing through a gap, counting parts, or triggering motion/tripwire alerts in DIY electronics projects.",
                "setup_info": "Power emitter (2 wires: red 3.3-5V, black GND) and receiver (3 wires: red 3.3-5V, black GND, white signal). Connect a 10k pull-up resistor between white signal and VCC (or enable INPUT_PULLUP on MCU). Signal pulls LOW when beam is broken.",
            },
            "local",
            "test-model",
        )

    monkeypatch.setattr(bundle_mod.item_bundle_service, "_fetch_public_url_text", fake_fetch_url)
    monkeypatch.setattr(bundle_mod.item_bundle_service, "_invoke_bundle_ai", fake_call_ai)

    pdf_bytes = _make_pdf_bytes(["Datasheet ADA-2167", "Supply 3.3V-5.5V DC", "Output NPN open-collector"])
    img1 = _make_png_bytes((10, 100, 200))
    img2 = _make_png_bytes((200, 80, 20))

    resp = client.post(
        "/api/v1/items/analyze-bundle",
        files=[
            ("files", ("break-beam-datasheet.pdf", pdf_bytes, "application/pdf")),
            ("files", ("screenshot.png", img1, "image/png")),
            ("files", ("photo2.png", img2, "image/png")),
        ],
        data={
            "location_id": str(loc["id"]),
            "urls": "https://www.adafruit.com/product/2167\nhttp://127.0.0.1:8000/internal-secret",
            "pasted_text": "Ordered 3 pairs from Adafruit. Use with Arduino INPUT_PULLUP.",
            "provider": "local",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["name"] == "IR Break-Beam Sensor (3mm LEDs)"
    assert body["part_number"] == "ADA-2167"
    assert body["quantity"] == 3
    assert body["unit"] == "pair"
    assert "tripwire" in body["use_case"]
    assert "INPUT_PULLUP" in body["setup_info"]
    assert "Use Case:" in body["description"]
    assert "Setup & Usage:" in body["description"]
    assert "https://www.adafruit.com/product/2167" in body["reference_links"]

    assert len(body["saved_images"]) == 2
    assert len(body["saved_documents"]) == 1
    assert body["saved_documents"][0]["attachment_type"] == "pdf"
    assert captured["image_count"] == 2
    assert "Datasheet ADA-2167" in captured["prompt"]
    assert "Ordered 3 pairs" in captured["prompt"]
    assert captured["fetched_urls"] == ["https://www.adafruit.com/product/2167"]


def test_analyze_bundle_leaves_quantity_none_when_unknown(client, monkeypatch):
    loc = client.post("/api/v1/locations", json={"name": "B16", "location_type": "bin"}).json()

    async def fake_call_ai(prompt: str, image_paths, *, provider=None, model=None):
        return (
            {
                "name": "Generic Buck Converter",
                "part_number": "LM2596",
                "category": "Power",
                "quantity": None,
                "unit": "pcs",
                "specs": "Input 4-40V, Output 1.25-37V adjustable",
                "use_case": "Step-down DC voltage regulation.",
                "setup_info": "Turn blue potentiometer counter-clockwise to lower output voltage before connecting load.",
            },
            "local",
            "test-model",
        )

    monkeypatch.setattr(bundle_mod.item_bundle_service, "_invoke_bundle_ai", fake_call_ai)

    resp = client.post(
        "/api/v1/items/analyze-bundle",
        data={"location_id": str(loc["id"]), "pasted_text": "LM2596 adjustable step-down module"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["quantity"] is None
    assert body["name"] == "Generic Buck Converter"


def test_create_item_from_bundle_moves_images_to_photos_and_pdfs_to_documents(client):
    loc = client.post("/api/v1/locations", json={"name": "B17", "location_type": "bin"}).json()

    img1 = client.post(
        "/api/v1/photos/upload",
        files={"file": ("main.png", _make_png_bytes((20, 180, 90)), "image/png")},
    ).json()
    img2 = client.post(
        "/api/v1/photos/upload",
        files={"file": ("wiring.png", _make_png_bytes((180, 40, 90)), "image/png")},
    ).json()
    tmp_item = client.post("/api/v1/items", json={"name": "Tmp", "location_id": loc["id"]}).json()
    pdf_att = client.post(
        f"/api/v1/items/{tmp_item['id']}/attachments",
        files={"file": ("manual.pdf", _make_pdf_bytes(["Manual page 1"]), "application/pdf")},
    ).json()

    create_resp = client.post(
        "/api/v1/items/from-bundle",
        json={
            "name": "IR Break-Beam Sensor",
            "location_id": loc["id"],
            "quantity": 2,
            "unit": "pair",
            "category": "Sensors",
            "serial_or_part_number": "ADA-2167",
            "tags": "ir, sensor",
            "specs": "3.3V-5V DC, 25cm range",
            "use_case": "Object detection across a beam.",
            "setup_info": "Use 10k pull-up resistor on white signal wire.",
            "reference_links": ["https://www.adafruit.com/product/2167"],
            "main_photo_path": img2["original_path"],
            "image_paths": [img1["original_path"], img2["original_path"]],
            "documents": [
                {
                    "file_path": pdf_att["file_path"],
                    "filename": "manual.pdf",
                    "title": "Break-Beam Manual",
                }
            ],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    assert created["name"] == "IR Break-Beam Sensor"
    assert created["quantity"] == 2
    assert created["item_photo"] == img2["original_path"]
    assert "Use Case:" in created["description"]
    assert "Setup & Usage:" in created["description"]
    assert "https://www.adafruit.com/product/2167" in created["description"]

    atts = client.get(f"/api/v1/items/{created['id']}/attachments").json()
    img_atts = [a for a in atts if a["attachment_type"] == "image"]
    pdf_atts = [a for a in atts if a["attachment_type"] == "pdf"]
    assert len(img_atts) == 2
    assert len(pdf_atts) == 1
    assert pdf_atts[0]["file_path"] == pdf_att["file_path"]
    assert pdf_atts[0]["title"] == "Break-Beam Manual"


def test_notes_renderer_turns_urls_into_hyperlinks_without_splitting_https():
    from pathlib import Path
    import shutil
    import subprocess
    import pytest

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")

    js_dir = (Path(__file__).resolve().parent.parent / "app" / "static" / "js").as_posix()
    script = f"""
    globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
    globalThis.BroadcastChannel = undefined;
    globalThis.requestAnimationFrame = () => {{}};
    const ws = {{ innerHTML: '', scrollTop: 0, classList: {{ remove() {{}}, add() {{}} }} }};
    globalThis.document = {{
      getElementById: (id) => (id === 'app-workspace' ? ws : null),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {{}},
      createElement: () => ({{ style: {{}}, classList: {{ add() {{}}, remove() {{}} }} }}),
    }};
    globalThis.window = {{ addEventListener() {{}}, location: {{ host: 'localhost:8000' }} }};
    const stateMod = await import('file://{js_dir}/state.js');
    const viewsMod = await import('file://{js_dir}/views.js');
    stateMod.state.items = [{{
      id: 99,
      name: 'IR Break Beam Sensor',
      location_id: null,
      status: 'stored',
      quantity: 5,
      unit: 'pcs',
      last_seen_at: '2026-11-27T00:00:00Z',
      description: 'Specs:\\nDC 3-5V\\n\\nLinks:\\nhttps://www.amazon.com/dp/B0FPD2NZPL?th=1',
    }}];
    viewsMod.renderItemView(99);
    if (ws.innerHTML.includes('<dt class="spec-parsed-k">https</dt>')) {{
      throw new Error('URL was wrongly split on https: into key-value row');
    }}
    if (!ws.innerHTML.includes('<a class="item-note-link" href="https://www.amazon.com/dp/B0FPD2NZPL?th=1" target="_blank" rel="noopener noreferrer">')) {{
      throw new Error('Expected clickable hyperlink in Notes HTML: ' + ws.innerHTML);
    }}
    process.exit(0);
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr


def test_ollama_http_400_with_images_retries_text_only(client, monkeypatch):
    import httpx

    loc = client.post("/api/v1/locations", json={"name": "B9", "location_type": "bin"}).json()
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, **kwargs):
            calls.append(json)
            if len(calls) == 1:
                return FakeResponse(400, {"error": "multimodal payload rejected"})
            return FakeResponse(
                200,
                {
                    "message": {
                        "content": '{"name": "Recovered Sensor", "quantity": 5, "unit": "pcs", "use_case": "Beam detection"}'
                    }
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    resp = client.post(
        "/api/v1/items/analyze-bundle",
        files=[("files", ("shot.png", _make_png_bytes(), "image/png"))],
        data={"location_id": str(loc["id"]), "pasted_text": "5pcs IR sensor", "provider": "local"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["name"] == "Recovered Sensor"
    assert body["quantity"] == 5
    assert len(calls) == 2
    assert "images" in calls[0]["messages"][0]
    assert "images" not in calls[1]["messages"][0]
