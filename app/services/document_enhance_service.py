import math
from pathlib import Path
from typing import Any, Optional
import cv2
import numpy as np
from PIL import Image, ImageOps
from app.core.exceptions import BadRequestError, NotFoundError
from app.schemas.document import DocumentEnhanceOptions
from app.services.document_ocr_service import document_ocr_service
from app.services.photo_service import photo_service

_PAPER_ASPECTS = {
    "letter": 8.5 / 11.0,
    "a4": 210.0 / 297.0,
    "legal": 8.5 / 14.0,
    "square": 1.0,
}


def load_bgr_image(photo_path: str) -> np.ndarray:
    """Resolve a /uploads/... path safely and decode into an EXIF-oriented BGR numpy array."""
    disk_path: Path = photo_service.resolve_safe_upload_path(photo_path)
    if not disk_path.exists() or not disk_path.is_file():
        raise NotFoundError(f"Photo not found: {photo_path}")
    try:
        with Image.open(disk_path) as im:
            oriented = ImageOps.exif_transpose(im).convert("RGB")
            rgb = np.asarray(oriented, dtype=np.uint8)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as exc:
        raise BadRequestError(f"Could not read image '{disk_path.name}': {exc}") from exc


def bgr_to_pil(img_bgr: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points deterministically as [TL, TR, BR, BL]."""
    arr = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = arr.sum(axis=1)
    diff = np.diff(arr, axis=1).reshape(4)
    tl = arr[int(np.argmin(s))]
    br = arr[int(np.argmax(s))]
    tr = arr[int(np.argmin(diff))]
    bl = arr[int(np.argmax(diff))]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _is_valid_document_quad(
    quad: np.ndarray,
    img_w: int,
    img_h: int,
    gray: Optional[np.ndarray] = None,
) -> tuple[bool, float]:
    ordered = order_quad_points(quad)
    area = float(cv2.contourArea(ordered))
    frame_area = float(img_w * img_h)
    if frame_area <= 0:
        return False, 0.0
    area_frac = area / frame_area
    if area_frac < 0.35 or area_frac > 0.99:
        return False, 0.0
    if not cv2.isContourConvex(ordered.astype(np.int32)):
        return False, 0.0

    # Verify interior angles are reasonably rectangular (50..130 deg)
    for i in range(4):
        p_prev = ordered[(i - 1) % 4]
        p_curr = ordered[i]
        p_next = ordered[(i + 1) % 4]
        v1 = p_prev - p_curr
        v2 = p_next - p_curr
        n1 = float(np.linalg.norm(v1))
        n2 = float(np.linalg.norm(v2))
        if n1 < 12 or n2 < 12:
            return False, 0.0
        cos_a = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
        deg = math.degrees(math.acos(cos_a))
        if deg < 50.0 or deg > 130.0:
            return False, 0.0

    # Reject internal sub-boxes on already-cropped pages:
    # A real document boundary separates brighter paper inside from a darker/different background outside.
    if gray is not None and area_frac < 0.92:
        mask = np.zeros((img_h, img_w), dtype=np.uint8)
        cv2.fillConvexPoly(mask, ordered.astype(np.int32), 255)
        inside_pixels = gray[mask > 0]
        outside_pixels = gray[mask == 0]
        if inside_pixels.size > 50 and outside_pixels.size > 50:
            inside_med = float(np.median(inside_pixels))
            outside_med = float(np.median(outside_pixels))
            # If the outside is almost as bright as the inside (paper on paper), this is an internal box
            if (inside_med - outside_med) < 18.0 and outside_med > 140.0:
                return False, 0.0

    # Measure closeness to frame border (avoid trivially picking image edges when content fills frame)
    xs = ordered[:, 0] / max(1.0, float(img_w))
    ys = ordered[:, 1] / max(1.0, float(img_h))
    near_border_corners = sum(
        1 for x, y in zip(xs, ys) if (x < 0.015 or x > 0.985) and (y < 0.015 or y > 0.985)
    )
    border_penalty = 0.72 if near_border_corners == 4 else 1.0
    rect_area = float(cv2.minAreaRect(ordered)[1][0] * cv2.minAreaRect(ordered)[1][1])
    rectangularity = area / max(1.0, rect_area)
    score = area_frac * rectangularity * border_penalty
    return True, float(min(1.0, score * 1.15))


def detect_document_quad(img_bgr: np.ndarray) -> tuple[list[list[float]], bool, float]:
    """Detect the document boundary as 4 normalized [TL, TR, BR, BL] points."""
    orig_h, orig_w = img_bgr.shape[:2]
    max_dim = max(orig_w, orig_h)
    scale = min(1.0, 900.0 / max(1, max_dim))
    small_w = max(32, int(round(orig_w * scale)))
    small_h = max(32, int(round(orig_h * scale)))
    small = cv2.resize(img_bgr, (small_w, small_h), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.bilateralFilter(gray, 9, 50, 50)
    bg = cv2.GaussianBlur(blur, (0, 0), sigmaX=max(11, int(round(min(small_w, small_h) * 0.05))))
    norm_gray = np.clip((blur.astype(np.float32) / np.maximum(bg.astype(np.float32), 1.0)) * 220.0, 0, 255).astype(
        np.uint8
    )

    candidate_maps: list[np.ndarray] = []
    for lo, hi in ((40, 130), (25, 90)):
        edges = cv2.Canny(norm_gray, lo, hi)
        edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=1)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
        candidate_maps.append(edges)

    # White-paper vs darker/colored background segmentation
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    v_chan = hsv[:, :, 2]
    s_chan = hsv[:, :, 1]
    v_thresh = max(110, int(np.percentile(v_chan, 42)))
    paper_mask = np.where((v_chan >= v_thresh) & (s_chan <= 95), 255, 0).astype(np.uint8)
    paper_mask = cv2.morphologyEx(paper_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)))
    paper_mask = cv2.morphologyEx(paper_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
    candidate_maps.append(paper_mask)

    best_quad: Optional[np.ndarray] = None
    best_score = 0.0

    for edge_map in candidate_maps:
        contours, _ = cv2.findContours(edge_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]
        for cnt in contours:
            peri = cv2.arcLength(cnt, True)
            if peri < 40:
                continue
            for eps in (0.02, 0.035, 0.05):
                approx = cv2.approxPolyDP(cnt, eps * peri, True)
                if len(approx) == 4:
                    ok, score = _is_valid_document_quad(approx.reshape(4, 2), small_w, small_h, gray=gray)
                    if ok and score > best_score:
                        best_score = score
                        best_quad = order_quad_points(approx.reshape(4, 2))
                    break

    if best_quad is None:
        return [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], False, 0.0

    norm_pts = [
        [float(np.clip(pt[0] / small_w, 0.0, 1.0)), float(np.clip(pt[1] / small_h, 0.0, 1.0))]
        for pt in best_quad
    ]
    return norm_pts, True, round(float(best_score), 3)


def _estimate_physical_aspect_ratio(ordered_px: np.ndarray, img_w: int, img_h: int) -> float:
    """Estimate document width/height ratio accounting for mild perspective foreshortening."""
    tl, tr, br, bl = ordered_px
    top_w = float(np.linalg.norm(tr - tl))
    bot_w = float(np.linalg.norm(br - bl))
    left_h = float(np.linalg.norm(bl - tl))
    right_h = float(np.linalg.norm(br - tr))
    avg_w = max(1.0, (top_w + bot_w) * 0.5)
    avg_h = max(1.0, (left_h + right_h) * 0.5)
    raw_ratio = avg_w / avg_h

    # Zhang's single-view rectangle metric approximation when perspective convergence exists
    cx, cy = img_w * 0.5, img_h * 0.5
    p1 = np.array([tl[0] - cx, tl[1] - cy, 1.0], dtype=np.float64)
    p2 = np.array([tr[0] - cx, tr[1] - cy, 1.0], dtype=np.float64)
    p3 = np.array([bl[0] - cx, bl[1] - cy, 1.0], dtype=np.float64)
    p4 = np.array([br[0] - cx, br[1] - cy, 1.0], dtype=np.float64)

    try:
        k2 = float(np.dot(np.cross(p1, p4), p3) / np.dot(np.cross(p2, p4), p3))
        k3 = float(np.dot(np.cross(p1, p4), p2) / np.dot(np.cross(p3, p4), p2))
        n2 = k2 * p2 - p1
        n3 = k3 * p3 - p1
        if abs(n2[2] * n3[2]) > 1e-6:
            f_sq = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2])
            if 100.0 <= f_sq <= 1e8:
                u_norm = math.sqrt(n2[0] ** 2 + n2[1] ** 2 + f_sq * (n2[2] ** 2))
                v_norm = math.sqrt(n3[0] ** 2 + n3[1] ** 2 + f_sq * (n3[2] ** 2))
                if v_norm > 1e-6:
                    zhang_ratio = u_norm / v_norm
                    if 0.35 <= zhang_ratio <= 2.8 and abs(zhang_ratio - raw_ratio) / max(raw_ratio, 1e-3) < 0.35:
                        return float(zhang_ratio)
    except Exception:
        pass
    return float(raw_ratio)


def suggest_aspect_name(ratio: float) -> str:
    inv_or_direct = ratio if ratio <= 1.0 else (1.0 / max(ratio, 1e-6))
    for name, target in (("letter", _PAPER_ASPECTS["letter"]), ("a4", _PAPER_ASPECTS["a4"]), ("legal", _PAPER_ASPECTS["legal"])):
        if abs(inv_or_direct - target) / target <= 0.06:
            return name
    if abs(inv_or_direct - 1.0) <= 0.06:
        return "square"
    return "auto"


def compute_target_aspect_dimensions(
    ordered_px: np.ndarray,
    img_shape: tuple[int, int],
    aspect_mode: str = "auto",
    max_dimension: int = 3200,
) -> tuple[int, int]:
    img_h, img_w = img_shape
    tl, tr, br, bl = ordered_px
    top_w = float(np.linalg.norm(tr - tl))
    bot_w = float(np.linalg.norm(br - bl))
    left_h = float(np.linalg.norm(bl - tl))
    right_h = float(np.linalg.norm(br - tr))

    base_w = max(32.0, max(top_w, bot_w))
    base_h = max(32.0, max(left_h, right_h))
    est_ratio = _estimate_physical_aspect_ratio(ordered_px, img_w, img_h)

    if aspect_mode == "original":
        target_ratio = (top_w + bot_w) / max(1.0, left_h + right_h)
    elif aspect_mode in _PAPER_ASPECTS:
        std_ratio = _PAPER_ASPECTS[aspect_mode]
        target_ratio = std_ratio if est_ratio <= 1.0 else (1.0 / std_ratio)
    else:
        snapped = suggest_aspect_name(est_ratio)
        if snapped in _PAPER_ASPECTS:
            std_ratio = _PAPER_ASPECTS[snapped]
            target_ratio = std_ratio if est_ratio <= 1.0 else (1.0 / std_ratio)
        else:
            target_ratio = est_ratio

    target_ratio = float(np.clip(target_ratio, 0.25, 4.0))
    if target_ratio >= 1.0:
        out_w = int(round(max(base_w, base_h * target_ratio)))
        out_h = int(round(out_w / target_ratio))
    else:
        out_h = int(round(max(base_h, base_w / target_ratio)))
        out_w = int(round(out_h * target_ratio))

    scale = min(1.0, float(max_dimension) / max(1, out_w, out_h))
    out_w = max(64, int(round(out_w * scale)))
    out_h = max(64, int(round(out_h * scale)))
    return out_w, out_h


def warp_perspective(
    img_bgr: np.ndarray,
    quad_norm: list[list[float]],
    *,
    aspect_mode: str = "auto",
    max_dimension: int = 3200,
) -> np.ndarray:
    img_h, img_w = img_bgr.shape[:2]
    src_pts = np.array([[pt[0] * img_w, pt[1] * img_h] for pt in quad_norm], dtype=np.float32)
    ordered = order_quad_points(src_pts)
    out_w, out_h = compute_target_aspect_dimensions(
        ordered,
        (img_h, img_w),
        aspect_mode=aspect_mode,
        max_dimension=max_dimension,
    )
    dst_pts = np.array(
        [[0.0, 0.0], [out_w - 1.0, 0.0], [out_w - 1.0, out_h - 1.0], [0.0, out_h - 1.0]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(ordered, dst_pts)
    return cv2.warpPerspective(
        img_bgr,
        matrix,
        (out_w, out_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def estimate_skew_angle(img_bgr: np.ndarray) -> float:
    """Estimate small text skew angle in degrees within [-15, +15]."""
    h, w = img_bgr.shape[:2]
    scale = min(1.0, 900.0 / max(1, w, h))
    small = cv2.resize(img_bgr, (max(32, int(w * scale)), max(32, int(h * scale))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    bg = cv2.GaussianBlur(blur, (0, 0), sigmaX=max(11, int(min(small.shape[:2]) * 0.04)))
    diff = np.clip(bg.astype(np.int16) - blur.astype(np.int16), 0, 255).astype(np.uint8)
    _, binary = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Ignore 3% outer margin so frame borders do not bias skew projection
    mh = max(4, int(binary.shape[0] * 0.03))
    mw = max(4, int(binary.shape[1] * 0.03))
    cropped = binary[mh:-mh, mw:-mw]
    if cropped.size == 0 or int(np.count_nonzero(cropped)) < 40:
        return 0.0

    # Dilate horizontally slightly to bind words into text lines
    line_mask = cv2.dilate(cropped, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)), iterations=1)
    ch, cw = line_mask.shape
    center = (cw * 0.5, ch * 0.5)

    def _proj_score(deg: float) -> float:
        mat = cv2.getRotationMatrix2D(center, deg, 1.0)
        rot = cv2.warpAffine(line_mask, mat, (cw, ch), flags=cv2.INTER_NEAREST, borderValue=0)
        row_sums = rot.sum(axis=1, dtype=np.float64)
        return float(np.var(row_sums))

    base_score = _proj_score(0.0)
    best_deg = 0.0
    best_score = base_score

    for deg in np.arange(-15.0, 15.5, 1.0):
        sc = _proj_score(float(deg))
        if sc > best_score:
            best_score = sc
            best_deg = float(deg)

    coarse_center = best_deg
    for deg in np.arange(max(-15.0, coarse_center - 1.2), min(15.0, coarse_center + 1.25), 0.1):
        sc = _proj_score(float(deg))
        if sc > best_score:
            best_score = sc
            best_deg = float(deg)

    if abs(best_deg) < 0.2 or best_score <= base_score * 1.03:
        return 0.0
    return round(float(best_deg), 2)


def rotate_and_deskew(img_bgr: np.ndarray, rotation_deg: int = 0, angle_deg: float = 0.0) -> np.ndarray:
    out = img_bgr
    rot_norm = int(rotation_deg) % 360
    if rot_norm == 90:
        out = cv2.rotate(out, cv2.ROTATE_90_CLOCKWISE)
    elif rot_norm == 180:
        out = cv2.rotate(out, cv2.ROTATE_180)
    elif rot_norm == 270:
        out = cv2.rotate(out, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if abs(angle_deg) < 0.08:
        return out

    h, w = out.shape[:2]
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    bright_mask = gray >= np.percentile(gray, 75)
    if np.any(bright_mask):
        border_bgr = tuple(int(v) for v in np.median(out[bright_mask], axis=0))
    else:
        border_bgr = (255, 255, 255)

    mat = cv2.getRotationMatrix2D((w * 0.5, h * 0.5), float(angle_deg), 1.0)
    return cv2.warpAffine(
        out,
        mat,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_bgr,
    )


def remove_shading_and_lighting(img_bgr: np.ndarray, strength: float = 0.85) -> np.ndarray:
    """Flatten uneven illumination, shadows, and lighting gradients while preserving ink & diagram color."""
    s = float(np.clip(strength, 0.0, 1.0))
    if s <= 0.01:
        return img_bgr

    h, w = img_bgr.shape[:2]
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)

    # Estimate smooth paper background illumination via morphological close + large Gaussian blur
    k_morph = max(15, int(round(max(w, h) * 0.025)) | 1)
    k_morph = min(65, k_morph)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_morph, k_morph))
    bg = cv2.morphologyEx(l_chan, cv2.MORPH_CLOSE, kernel)

    sigma = max(15.0, min(80.0, max(w, h) * 0.035))
    bg = cv2.GaussianBlur(bg, (0, 0), sigmaX=sigma, sigmaY=sigma)

    l_f = l_chan.astype(np.float32)
    bg_f = np.maximum(bg.astype(np.float32), 8.0)
    l_flat = np.clip((l_f / bg_f) * 252.0, 0.0, 255.0)
    l_out = np.clip((1.0 - s) * l_f + s * l_flat, 0.0, 255.0).astype(np.uint8)

    # Gently neutralize paper color cast on bright background pixels while preserving saturated graphics
    a_f = a_chan.astype(np.float32) - 128.0
    b_f = b_chan.astype(np.float32) - 128.0
    chroma = np.sqrt(a_f ** 2 + b_f ** 2)
    paper_weight = np.clip((l_flat - 215.0) / 35.0, 0.0, 1.0) * np.clip(1.0 - (chroma / 28.0), 0.0, 1.0) * s
    a_out = np.clip(128.0 + a_f * (1.0 - 0.85 * paper_weight), 0.0, 255.0).astype(np.uint8)
    b_out = np.clip(128.0 + b_f * (1.0 - 0.85 * paper_weight), 0.0, 255.0).astype(np.uint8)

    merged = cv2.merge([l_out, a_out, b_out])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def white_balance_paper(img_bgr: np.ndarray) -> np.ndarray:
    """Shift bright paper tone toward neutral white without clipping dark ink."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    thresh = np.percentile(gray, 85)
    mask = gray >= thresh
    if int(np.count_nonzero(mask)) < 32:
        return img_bgr

    bright_pixels = img_bgr[mask].astype(np.float32)
    est_white = np.percentile(bright_pixels, 92, axis=0)
    target = float(np.clip(np.max(est_white), 210.0, 252.0))
    gains = np.clip(target / np.maximum(est_white, 20.0), 0.85, 1.25)
    balanced = np.clip(img_bgr.astype(np.float32) * gains.reshape(1, 1, 3), 0.0, 255.0)
    return balanced.astype(np.uint8)


def denoise_document(img_bgr: np.ndarray, strength: float = 0.45) -> np.ndarray:
    s = float(np.clip(strength, 0.0, 1.0))
    if s <= 0.02:
        return img_bgr
    h_lum = 2.0 + 7.5 * s
    h_col = 2.0 + 6.0 * s
    return cv2.fastNlMeansDenoisingColored(img_bgr, None, h_lum, h_col, 7, 15)


def _sauvola_soft_bw(gray: np.ndarray) -> np.ndarray:
    """Clean local adaptive B&W enhancement preserving smooth glyph edges and punctuation."""
    h, w = gray.shape
    win = max(21, int(round(min(w, h) * 0.03)) | 1)
    win = min(75, win)
    g_f = gray.astype(np.float32)
    mean = cv2.boxFilter(g_f, ddepth=-1, ksize=(win, win), normalize=True)
    mean_sq = cv2.boxFilter(g_f * g_f, ddepth=-1, ksize=(win, win), normalize=True)
    std = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0))
    thresh = mean * (1.0 + 0.16 * ((std / 128.0) - 1.0))
    # Soft ramp around threshold for crisp anti-aliased edges
    soft = np.clip(((g_f - (thresh - 14.0)) / 28.0) * 255.0, 0.0, 255.0).astype(np.uint8)

    # Remove tiny isolated 1-2px dust speckles in white paper regions
    inv = np.where(soft < 200, 255, 0).astype(np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    for lbl in range(1, num_labels):
        if int(stats[lbl, cv2.CC_STAT_AREA]) <= 2:
            soft[labels == lbl] = 255

    return cv2.cvtColor(soft, cv2.COLOR_GRAY2BGR)


def apply_preset_and_sharpen(img_bgr: np.ndarray, preset: str, sharpen_strength: float) -> np.ndarray:
    sharp = float(np.clip(sharpen_strength, 0.0, 1.0))
    out = img_bgr

    if preset == "clean_bw":
        gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        out = _sauvola_soft_bw(gray)
    elif preset == "grayscale_scan":
        gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        hi = max(180.0, float(np.percentile(gray, 92)))
        lo = min(60.0, float(np.percentile(gray, 2)))
        stretched = np.clip(((gray.astype(np.float32) - lo) / max(1.0, hi - lo)) * 255.0, 0.0, 255.0).astype(
            np.uint8
        )
        out = cv2.cvtColor(stretched, cv2.COLOR_GRAY2BGR)
    elif preset == "auto_document":
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
        l_eq = clahe.apply(l_chan)
        hi = max(195.0, float(np.percentile(l_eq, 90)))
        lo = min(55.0, float(np.percentile(l_eq, 2)))
        l_stretch = np.clip(((l_eq.astype(np.float32) - lo) / max(1.0, hi - lo)) * 255.0, 0.0, 255.0).astype(
            np.uint8
        )
        out = cv2.cvtColor(cv2.merge([l_stretch, a_chan, b_chan]), cv2.COLOR_LAB2BGR)

    if sharp > 0.02:
        blurred = cv2.GaussianBlur(out, (0, 0), sigmaX=1.1)
        alpha = 1.0 + 0.9 * sharp
        beta = -0.9 * sharp
        out = cv2.addWeighted(out, alpha, blurred, beta, 0)

    return out


class DocumentEnhanceService:
    """Modular OpenCV document straightening, shadow removal, denoising, and preset enhancement."""

    def detect_corners(self, photo_path: str) -> dict[str, Any]:
        img_bgr = load_bgr_image(photo_path)
        h, w = img_bgr.shape[:2]
        quad, detected, confidence = detect_document_quad(img_bgr)
        preview = warp_perspective(img_bgr, quad, aspect_mode="auto", max_dimension=1400) if detected else img_bgr
        skew = estimate_skew_angle(preview)
        ordered_px = np.array([[pt[0] * w, pt[1] * h] for pt in quad], dtype=np.float32)
        est_ratio = _estimate_physical_aspect_ratio(ordered_px, w, h)
        return {
            "quad": quad,
            "detected": detected,
            "confidence": confidence,
            "skew_angle_deg": skew,
            "width": w,
            "height": h,
            "suggested_aspect": suggest_aspect_name(est_ratio),
        }

    def enhance_image_array(
        self,
        img_bgr: np.ndarray,
        options: DocumentEnhanceOptions,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        warnings: list[str] = []
        applied_perspective = False
        detected_quad: Optional[list[list[float]]] = None

        work = img_bgr
        if options.quad is not None:
            detected_quad = options.quad
            work = warp_perspective(
                work,
                options.quad,
                aspect_mode=options.aspect_mode,
                max_dimension=options.max_dimension,
            )
            applied_perspective = True
        elif options.auto_perspective:
            quad, detected, _ = detect_document_quad(work)
            detected_quad = quad
            if detected:
                work = warp_perspective(
                    work,
                    quad,
                    aspect_mode=options.aspect_mode,
                    max_dimension=options.max_dimension,
                )
                applied_perspective = True

        # Determine 90-degree orientation (manual override or automatic upright text detection)
        applied_rotation_deg = int(options.rotation_deg or 0) % 360
        if options.auto_orient and applied_rotation_deg == 0:
            applied_rotation_deg = document_ocr_service.detect_upright_rotation(work)

        if applied_rotation_deg:
            work = rotate_and_deskew(work, rotation_deg=applied_rotation_deg, angle_deg=0.0)

        auto_angle = estimate_skew_angle(work) if options.auto_deskew else 0.0
        total_fine_angle = float(auto_angle + options.fine_angle_deg)
        if abs(total_fine_angle) >= 0.08:
            work = rotate_and_deskew(work, rotation_deg=0, angle_deg=total_fine_angle)

        if options.denoise_strength > 0.02:
            work = denoise_document(work, strength=options.denoise_strength)

        if options.remove_shading and options.shading_strength > 0.01:
            work = remove_shading_and_lighting(work, strength=options.shading_strength)

        if options.white_balance and options.preset != "photo_preserve":
            work = white_balance_paper(work)

        work = apply_preset_and_sharpen(work, options.preset, options.sharpen_strength)

        meta = {
            "applied_perspective": applied_perspective,
            "applied_rotation_deg": applied_rotation_deg,
            "applied_deskew_deg": round(total_fine_angle, 2),
            "detected_quad": detected_quad,
            "warnings": warnings,
        }
        return work, meta

    def enhance_photo_file(
        self,
        photo_path: str,
        options: Optional[DocumentEnhanceOptions] = None,
    ) -> dict[str, Any]:
        opts = options or DocumentEnhanceOptions()
        img_bgr = load_bgr_image(photo_path)
        enhanced_bgr, meta = self.enhance_image_array(img_bgr, opts)
        pil_out = bgr_to_pil(enhanced_bgr)
        saved = photo_service.save_pil_image(
            pil_out,
            ext=".png",
            max_dimension=opts.max_dimension,
            prefix="doc_",
        )
        return {
            "original_path": photo_path,
            "enhanced_path": saved["original_path"],
            "thumbnail_path": saved["thumbnail_path"],
            "width": saved["width"],
            "height": saved["height"],
            "applied_perspective": meta["applied_perspective"],
            "applied_rotation_deg": meta["applied_rotation_deg"],
            "applied_deskew_deg": meta["applied_deskew_deg"],
            "detected_quad": meta["detected_quad"],
            "warnings": meta["warnings"],
        }


document_enhance_service = DocumentEnhanceService()
