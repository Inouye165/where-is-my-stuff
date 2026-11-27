import cv2
import numpy as np
from app.schemas.document import DocumentEnhanceOptions
from app.services.document_enhance_service import (
    compute_target_aspect_dimensions,
    detect_document_quad,
    document_enhance_service,
    estimate_skew_angle,
    remove_shading_and_lighting,
    warp_perspective,
)


def _make_synthetic_skewed_shadowed_page() -> np.ndarray:
    """Create a realistic tilted, perspective-warped, shadowed white page on a dark desk."""
    page_w, page_h = 850, 1100
    page = np.full((page_h, page_w, 3), 248, dtype=np.uint8)

    # Printed lines of text + rule
    cv2.putText(page, "TECHNICAL SPECIFICATION", (90, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.35, (20, 20, 20), 3, cv2.LINE_AA)
    cv2.line(page, (90, 160), (760, 160), (40, 40, 40), 3)
    for row in range(12):
        y = 220 + row * 58
        cv2.putText(
            page,
            f"Item {row + 1}: Precision Voltage Regulator 5.0V 1.5A",
            (95, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.78,
            (25, 25, 25),
            2,
            cv2.LINE_AA,
        )

    # Apply diagonal lighting gradient + dark shadow across half the page
    yy, xx = np.mgrid[0:page_h, 0:page_w].astype(np.float32)
    illum = 1.0 - 0.48 * (xx / page_w) - 0.22 * (yy / page_h)
    shadowed = np.clip(page.astype(np.float32) * illum[:, :, None], 0, 255).astype(np.uint8)

    # Place warped page onto a dark wood-like background
    bg_w, bg_h = 1200, 1400
    scene = np.full((bg_h, bg_w, 3), (48, 56, 68), dtype=np.uint8)
    rng = np.random.default_rng(42)
    noise = rng.integers(-6, 7, size=scene.shape, dtype=np.int16)
    scene = np.clip(scene.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    src_quad = np.array([[0, 0], [page_w - 1, 0], [page_w - 1, page_h - 1], [0, page_h - 1]], dtype=np.float32)
    dst_quad = np.array([[180, 140], [990, 195], [1030, 1210], [150, 1245]], dtype=np.float32)
    h_mat = cv2.getPerspectiveTransform(src_quad, dst_quad)
    warped_page = cv2.warpPerspective(shadowed, h_mat, (bg_w, bg_h))
    mask = np.zeros((bg_h, bg_w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, dst_quad.astype(np.int32), 255)
    scene[mask > 0] = warped_page[mask > 0]
    return scene


def test_detect_document_quad_and_perspective_warp():
    scene = _make_synthetic_skewed_shadowed_page()
    quad, detected, confidence = detect_document_quad(scene)
    assert detected is True
    assert confidence > 0.35
    assert len(quad) == 4

    # Corners should stay well inside outer scene bounds rather than collapsing to [0..1]
    xs = [pt[0] for pt in quad]
    ys = [pt[1] for pt in quad]
    assert min(xs) > 0.05 and max(xs) < 0.95
    assert min(ys) > 0.05 and max(ys) < 0.95

    warped = warp_perspective(scene, quad, aspect_mode="letter", max_dimension=1600)
    wh, ww = warped.shape[:2]
    assert abs((ww / wh) - (8.5 / 11.0)) < 0.03


def test_aspect_modes_produce_expected_ratios():
    pts = np.array([[100, 100], [900, 110], [920, 1200], [90, 1210]], dtype=np.float32)
    for mode, expected in (("letter", 8.5 / 11.0), ("a4", 210.0 / 297.0), ("legal", 8.5 / 14.0), ("square", 1.0)):
        w, h = compute_target_aspect_dimensions(pts, (1400, 1100), aspect_mode=mode, max_dimension=2000)
        assert abs((w / h) - expected) < 0.02, f"Aspect mismatch for {mode}: {w}x{h}"


def test_estimate_skew_angle_detects_tilted_text():
    page = np.full((700, 900, 3), 252, dtype=np.uint8)
    for i in range(8):
        cv2.putText(
            page,
            f"CALIBRATION LINE {i} - PRECISION TEST DATA",
            (80, 110 + i * 65),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (20, 20, 20),
            2,
            cv2.LINE_AA,
        )
    rot_mat = cv2.getRotationMatrix2D((450, 350), 4.5, 1.0)
    tilted = cv2.warpAffine(page, rot_mat, (900, 700), borderValue=(252, 252, 252))

    est = estimate_skew_angle(tilted)
    # To straighten a +4.5 deg rotated image back to horizontal, corrective angle is near -4.5 deg
    assert -6.0 <= est <= -3.0, f"Expected corrective skew near -4.5 deg, got {est}"


def test_remove_shading_flattens_lighting_gradient():
    img = np.full((600, 800, 3), 245, dtype=np.uint8)
    cv2.putText(img, "UNIFORM PAPER TEST", (120, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 3, cv2.LINE_AA)
    xx = np.linspace(0.35, 1.0, 800, dtype=np.float32)[None, :, None]
    uneven = np.clip(img.astype(np.float32) * xx, 0, 255).astype(np.uint8)

    gray_before = cv2.cvtColor(uneven, cv2.COLOR_BGR2GRAY)
    bg_std_before = float(np.std(gray_before[gray_before > 70]))

    cleaned = remove_shading_and_lighting(uneven, strength=0.9)
    gray_after = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)
    bg_std_after = float(np.std(gray_after[gray_after > 140]))

    assert bg_std_after < bg_std_before * 0.45


def test_all_enhancement_presets_succeed():
    scene = _make_synthetic_skewed_shadowed_page()
    for preset in ("auto_document", "clean_bw", "grayscale_scan", "photo_preserve"):
        opts = DocumentEnhanceOptions(preset=preset, max_dimension=1400)
        out_img, meta = document_enhance_service.enhance_image_array(scene, opts)
        assert out_img.ndim == 3 and out_img.shape[2] == 3
        assert out_img.shape[0] > 200 and out_img.shape[1] > 200
        assert meta["applied_perspective"] is True


def test_auto_orient_rotates_sideways_and_upside_down_pages_upright():
    page = np.full((600, 800, 3), 252, dtype=np.uint8)
    cv2.putText(page, "Instructions for Use", (120, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 20, 20), 3, cv2.LINE_AA)
    cv2.putText(page, "Module Working Principle", (120, 280), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(page, "HC-SR04 Ultrasonic Sensor", (120, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2, cv2.LINE_AA)

    for rot_code, expected_fix in (
        (cv2.ROTATE_90_CLOCKWISE, 270),
        (cv2.ROTATE_180, 180),
        (cv2.ROTATE_90_COUNTERCLOCKWISE, 90),
    ):
        rotated = cv2.rotate(page, rot_code)
        opts = DocumentEnhanceOptions(
            auto_perspective=False,
            auto_orient=True,
            auto_deskew=False,
            denoise_strength=0.0,
            remove_shading=False,
            white_balance=False,
        )
        out_img, meta = document_enhance_service.enhance_image_array(rotated, opts)
        assert meta["applied_rotation_deg"] == expected_fix
        assert out_img.shape[1] > out_img.shape[0]


def test_detect_document_quad_does_not_crop_internal_box_on_already_cropped_page():
    """Regression: an already-cropped white page with a dark diagram/glare box must NOT crop into the sub-box."""
    page = np.full((900, 1200, 3), 232, dtype=np.uint8)
    cv2.putText(page, "Instructions for Use", (120, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (25, 25, 25), 3, cv2.LINE_AA)
    cv2.putText(page, "Module Working Principle", (120, 500), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (25, 25, 25), 2, cv2.LINE_AA)
    # High-contrast rectangular sub-box (like an ultrasonic sensor diagram) occupying ~22% of frame
    cv2.rectangle(page, (680, 180), (1120, 720), (35, 75, 130), -1)
    cv2.rectangle(page, (680, 180), (1120, 720), (20, 20, 20), 4)

    _quad, detected, _conf = detect_document_quad(page)
    assert detected is False
