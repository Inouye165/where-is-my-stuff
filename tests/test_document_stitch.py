import io
import cv2
import numpy as np
from PIL import Image


def _make_highres_doc_and_blurry_overview():
    """Create a sharp high-res master page, a blurry downscaled overview, and a sharp close-up patch."""
    master = np.full((1800, 1400, 3), 248, dtype=np.uint8)
    cv2.putText(master, "HC-SR04 SENSOR DATASHEET", (120, 150), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (18, 18, 18), 4, cv2.LINE_AA)
    cv2.line(master, (120, 190), (1280, 190), (30, 30, 30), 4)
    cv2.rectangle(master, (160, 260), (620, 620), (160, 90, 30), 6)
    cv2.circle(master, (300, 440), 75, (40, 40, 40), 5)
    cv2.circle(master, (480, 440), 75, (40, 40, 40), 5)

    # Fine print section in lower half with distinct technical lines
    lines = [
        "(1) Supply voltage 5V DC nominal, quiescent current < 2mA",
        "(2) Send 10us high TTL pulse to Trig pin to start ranging",
        "(3) Module automatically emits eight 40kHz ultrasonic bursts",
        "(4) Echo output stays high proportional to round-trip flight",
        "(5) Distance in cm = pulse_width_us / 58.0 (or inches / 148)",
        "(6) Recommended measurement cycle interval >= 60 milliseconds",
        "(7) Effective measuring angle is approximately 15 degrees",
        "(8) Operating range 2cm to 400cm with 3mm typical resolution",
    ]
    for i, text_line in enumerate(lines):
        y = 820 + i * 95
        cv2.putText(
            master,
            text_line,
            (130, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.95,
            (20, 20, 20),
            2,
            cv2.LINE_AA,
        )

    # Blurry low-res overview simulating far camera
    overview = cv2.GaussianBlur(master, (9, 9), 2.4)
    overview = cv2.resize(overview, (700, 900), interpolation=cv2.INTER_AREA)

    # Sharp close-up patch of lower fine-print region (slightly rotated + brighter lighting)
    crop = master[740:1660, 90:1320].copy()
    ch, cw = crop.shape[:2]
    rot = cv2.getRotationMatrix2D((cw * 0.5, ch * 0.5), 2.5, 1.0)
    patch = cv2.warpAffine(crop, rot, (cw, ch), borderValue=(248, 248, 248))

    def _to_png_bytes(bgr: np.ndarray) -> bytes:
        buf = io.BytesIO()
        Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(buf, format="PNG")
        return buf.getvalue()

    return _to_png_bytes(overview), _to_png_bytes(patch)


def test_stitch_closeup_patch_api_aligns_and_sharpens(client):
    overview_bytes, patch_bytes = _make_highres_doc_and_blurry_overview()

    base_up = client.post(
        "/api/v1/photos/upload?max_dimension=4096",
        files={"file": ("overview.png", overview_bytes, "image/png")},
    )
    assert base_up.status_code == 200
    base_path = base_up.json()["original_path"]

    patch_up = client.post(
        "/api/v1/photos/upload?max_dimension=4096",
        files={"file": ("closeup.png", patch_bytes, "image/png")},
    )
    assert patch_up.status_code == 200
    patch_path = patch_up.json()["original_path"]

    res = client.post(
        "/api/v1/documents/stitch-patch",
        json={
            "base_photo_path": base_path,
            "patch_photo_path": patch_path,
            "enhance_patch": True,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["stitched_path"].startswith("/uploads/originals/")
    assert data["inliers"] >= 12
    assert data["upscale_factor"] > 1.2
    x1, y1, x2, y2 = data["patch_bbox_norm"]
    # Patch should land in the lower half of the page
    assert y1 > 0.35 and y2 > 0.80 and (x2 - x1) > 0.65

    # Verify stitched image exists and is sharper in the fine-print region than the overview
    stitched_dl = client.get(data["stitched_path"])
    assert stitched_dl.status_code == 200
    stitched_im = cv2.imdecode(np.frombuffer(stitched_dl.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    overview_im = cv2.imdecode(np.frombuffer(overview_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    overview_up = cv2.resize(overview_im, (stitched_im.shape[1], stitched_im.shape[0]), interpolation=cv2.INTER_CUBIC)

    sh, sw = stitched_im.shape
    roi_stitched = stitched_im[int(sh * 0.45) : int(sh * 0.88), int(sw * 0.12) : int(sw * 0.88)]
    roi_overview = overview_up[int(sh * 0.45) : int(sh * 0.88), int(sw * 0.12) : int(sw * 0.88)]
    sharp_stitched = float(cv2.Laplacian(roi_stitched, cv2.CV_64F).var())
    sharp_overview = float(cv2.Laplacian(roi_overview, cv2.CV_64F).var())
    assert sharp_stitched > sharp_overview * 1.6
