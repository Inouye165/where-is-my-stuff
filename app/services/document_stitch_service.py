import math
from typing import Any
import cv2
import numpy as np
from app.core.exceptions import BadRequestError
from app.services.document_enhance_service import (
    bgr_to_pil,
    load_bgr_image,
    remove_shading_and_lighting,
    white_balance_paper,
)
from app.services.photo_service import photo_service


def _is_sane_quad(h_mat: np.ndarray, pw: int, ph: int, bw: int, bh: int) -> bool:
    patch_corners = np.array(
        [[[0.0, 0.0]], [[pw - 1.0, 0.0]], [[pw - 1.0, ph - 1.0]], [[0.0, ph - 1.0]]],
        dtype=np.float32,
    )
    warped = cv2.perspectiveTransform(patch_corners, h_mat).reshape(4, 2)
    if not np.all(np.isfinite(warped)):
        return False
    if not cv2.isContourConvex(warped.astype(np.int32)):
        return False
    warped_area = float(cv2.contourArea(warped.astype(np.float32)))
    area_frac = warped_area / max(1.0, float(bw * bh))
    return 0.015 <= area_frac <= 1.35


def _match_sift_homography(
    patch_bgr: np.ndarray,
    base_bgr: np.ndarray,
) -> tuple[np.ndarray, int, float]:
    """Find perspective (or affine fallback) transform H mapping full-res patch_bgr into full-res base_bgr."""
    ph, pw = patch_bgr.shape[:2]
    bh, bw = base_bgr.shape[:2]

    # Downscale working copies to <=1800px for fast, repeatable SIFT extraction
    p_scale = min(1.0, 1800.0 / max(1, pw, ph))
    b_scale = min(1.0, 1800.0 / max(1, bw, bh))
    p_small = (
        cv2.resize(patch_bgr, (max(64, int(round(pw * p_scale))), max(64, int(round(ph * p_scale)))), interpolation=cv2.INTER_AREA)
        if p_scale < 0.99
        else patch_bgr
    )
    b_small = (
        cv2.resize(base_bgr, (max(64, int(round(bw * b_scale))), max(64, int(round(bh * b_scale)))), interpolation=cv2.INTER_AREA)
        if b_scale < 0.99
        else base_bgr
    )

    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    p_gray = clahe.apply(cv2.cvtColor(p_small, cv2.COLOR_BGR2GRAY))
    b_gray = clahe.apply(cv2.cvtColor(b_small, cv2.COLOR_BGR2GRAY))

    sift = cv2.SIFT_create(nfeatures=5000)
    kp_p, desc_p = sift.detectAndCompute(p_gray, None)
    kp_b, desc_b = sift.detectAndCompute(b_gray, None)

    if desc_p is None or desc_b is None or len(kp_p) < 10 or len(kp_b) < 10:
        raise BadRequestError(
            "Not enough visual detail to align the close-up. Include a bit more text or edges and try again."
        )

    matcher = cv2.BFMatcher(cv2.NORM_L2)
    raw_matches = matcher.knnMatch(desc_p, desc_b, k=2)
    good = [pair[0] for pair in raw_matches if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance]

    match_sets: list[list[Any]] = []
    if len(good) >= 10:
        match_sets.append(good)
    # Fallback match set for repetitive text/tables where Lowe's ratio drops valid repeated-word matches
    best_by_dist = sorted(
        (pair[0] for pair in raw_matches if len(pair) >= 1),
        key=lambda m: m.distance,
    )[:350]
    if len(best_by_dist) >= 10:
        match_sets.append(best_by_dist)

    if not match_sets:
        raise BadRequestError(
            "Could not align the close-up to the page. Make sure the close-up overlaps recognizable text or graphics."
        )

    method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
    for candidates in match_sets:
        src_pts = np.float32([kp_p[m.queryIdx].pt for m in candidates]).reshape(-1, 1, 2) / p_scale
        dst_pts = np.float32([kp_b[m.trainIdx].pt for m in candidates]).reshape(-1, 1, 2) / b_scale

        # 1. Try MAGSAC perspective homography first
        h_mat, mask = cv2.findHomography(
            src_pts,
            dst_pts,
            method,
            ransacReprojThreshold=5.5,
            maxIters=4000,
            confidence=0.995,
        )
        if h_mat is not None and mask is not None:
            inliers = int(np.count_nonzero(mask))
            if inliers >= 12 and _is_sane_quad(h_mat, pw, ph, bw, bh):
                return h_mat, inliers, round(min(1.0, inliers / max(1.0, float(len(candidates)))), 3)

        # 2. Fallback to affine / similarity RANSAC (immune to projective bow-tie)
        for est_fn in (cv2.estimateAffinePartial2D, cv2.estimateAffine2D):
            aff, aff_mask = est_fn(
                src_pts,
                dst_pts,
                method=cv2.RANSAC,
                ransacReprojThreshold=6.5,
                maxIters=4000,
                confidence=0.995,
            )
            if aff is not None and aff_mask is not None:
                inliers = int(np.count_nonzero(aff_mask))
                h_aff = np.vstack([aff, np.array([[0.0, 0.0, 1.0]], dtype=np.float64)])
                if inliers >= 10 and _is_sane_quad(h_aff, pw, ph, bw, bh):
                    return h_aff, inliers, round(min(1.0, inliers / max(1.0, float(len(candidates)))), 3)

    raise BadRequestError(
        "Could not align the close-up cleanly to the page. Try holding the camera flatter and including a little more surrounding text."
    )


def _match_patch_tone_to_base(
    warped_patch: np.ndarray,
    base_canvas: np.ndarray,
    valid_mask: np.ndarray,
) -> np.ndarray:
    """Adjust warped patch luminance & tint in CIELAB to blend seamlessly with the base page."""
    overlap = valid_mask > 0
    if int(np.count_nonzero(overlap)) < 100:
        return warped_patch

    patch_lab = cv2.cvtColor(warped_patch, cv2.COLOR_BGR2LAB).astype(np.float32)
    base_lab = cv2.cvtColor(base_canvas, cv2.COLOR_BGR2LAB).astype(np.float32)

    p_l = patch_lab[:, :, 0][overlap]
    b_l = base_lab[:, :, 0][overlap]
    p_hi, p_lo = float(np.percentile(p_l, 90)), float(np.percentile(p_l, 15))
    b_hi, b_lo = float(np.percentile(b_l, 90)), float(np.percentile(b_l, 15))

    p_span = max(12.0, p_hi - p_lo)
    b_span = max(12.0, b_hi - b_lo)
    gain = float(np.clip(b_span / p_span, 0.78, 1.32))
    offset = b_hi - p_hi * gain
    patch_lab[:, :, 0] = np.clip(patch_lab[:, :, 0] * gain + offset, 0.0, 255.0)

    # Gently align paper tint (a, b channels)
    for ch in (1, 2):
        delta = float(np.median(base_lab[:, :, ch][overlap]) - np.median(patch_lab[:, :, ch][overlap]))
        delta = float(np.clip(delta, -14.0, 14.0))
        patch_lab[:, :, ch] = np.clip(patch_lab[:, :, ch] + delta, 0.0, 255.0)

    return cv2.cvtColor(patch_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


class DocumentStitchService:
    """Aligns and seamlessly stitches higher-resolution close-up patches onto a document page."""

    def stitch_closeup_patch(
        self,
        *,
        base_photo_path: str,
        patch_photo_path: str,
        enhance_patch: bool = True,
        max_dimension: int = 4096,
    ) -> dict[str, Any]:
        base_bgr = load_bgr_image(base_photo_path)
        patch_bgr = load_bgr_image(patch_photo_path)

        if enhance_patch:
            patch_bgr = remove_shading_and_lighting(patch_bgr, strength=0.72)
            patch_bgr = white_balance_paper(patch_bgr)

        ph, pw = patch_bgr.shape[:2]
        bh, bw = base_bgr.shape[:2]

        h_mat, inliers, confidence = _match_sift_homography(patch_bgr, base_bgr)

        # Validate warped patch quad geometry on base page
        patch_corners = np.array(
            [[[0.0, 0.0]], [[pw - 1.0, 0.0]], [[pw - 1.0, ph - 1.0]], [[0.0, ph - 1.0]]],
            dtype=np.float32,
        )
        warped_corners = cv2.perspectiveTransform(patch_corners, h_mat).reshape(4, 2)
        if not np.all(np.isfinite(warped_corners)):
            raise BadRequestError("Close-up alignment produced invalid geometry.")
        if not cv2.isContourConvex(warped_corners.astype(np.int32)):
            raise BadRequestError("Close-up alignment was distorted; try holding the camera flatter over the page.")

        warped_area = float(cv2.contourArea(warped_corners.astype(np.float32)))
        base_area = float(bw * bh)
        area_frac = warped_area / max(1.0, base_area)
        if area_frac < 0.015 or area_frac > 1.35:
            raise BadRequestError("Close-up scale did not match the document page cleanly. Try again slightly closer or farther.")

        # Determine if upscaling the base page preserves extra DPI from the close-up patch
        patch_area = float(pw * ph)
        detail_mag = math.sqrt(max(1.0, patch_area) / max(1.0, warped_area))
        max_allowed_scale = float(max_dimension) / max(1.0, float(max(bw, bh)))
        upscale_factor = 1.0
        if detail_mag > 1.15 and max_allowed_scale > 1.05:
            upscale_factor = min(detail_mag, max_allowed_scale, 2.5)

        if upscale_factor > 1.04:
            out_w = int(round(bw * upscale_factor))
            out_h = int(round(bh * upscale_factor))
            canvas_base = cv2.resize(base_bgr, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
            scale_mat = np.array([[upscale_factor, 0.0, 0.0], [0.0, upscale_factor, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
            h_scaled = scale_mat @ h_mat
        else:
            out_w, out_h = bw, bh
            canvas_base = base_bgr.copy()
            h_scaled = h_mat

        warped_patch = cv2.warpPerspective(
            patch_bgr,
            h_scaled,
            (out_w, out_h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

        # Build crisp inner mask + feathered distance-transform border for invisible seam blending
        ones_mask = np.full((ph, pw), 255, dtype=np.uint8)
        warped_mask = cv2.warpPerspective(
            ones_mask,
            h_scaled,
            (out_w, out_h),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        # Drop 4px outer fringe to avoid perspective edge ringing
        erode_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        inner_mask = cv2.erode(warped_mask, erode_k, iterations=1)
        if int(np.count_nonzero(inner_mask)) < 200:
            raise BadRequestError("Close-up did not overlap enough of the base page.")

        toned_patch = _match_patch_tone_to_base(warped_patch, canvas_base, inner_mask)

        dist = cv2.distanceTransform(inner_mask, cv2.DIST_L2, 5)
        ys, xs = np.where(inner_mask > 0)
        box_w = int(xs.max() - xs.min() + 1)
        box_h = int(ys.max() - ys.min() + 1)
        feather_px = float(max(12, min(40, min(box_w, box_h) // 10)))
        alpha = np.clip(dist / feather_px, 0.0, 1.0).astype(np.float32)
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=max(2.0, feather_px * 0.18))
        alpha_3 = alpha[:, :, None]

        blended = np.clip(
            toned_patch.astype(np.float32) * alpha_3 + canvas_base.astype(np.float32) * (1.0 - alpha_3),
            0.0,
            255.0,
        ).astype(np.uint8)

        saved = photo_service.save_pil_image(
            bgr_to_pil(blended),
            ext=".png",
            max_dimension=max_dimension,
            prefix="stitch_",
        )

        bbox_norm = [
            round(float(xs.min()) / out_w, 4),
            round(float(ys.min()) / out_h, 4),
            round(float(xs.max()) / out_w, 4),
            round(float(ys.max()) / out_h, 4),
        ]
        return {
            "stitched_path": saved["original_path"],
            "thumbnail_path": saved["thumbnail_path"],
            "width": saved["width"],
            "height": saved["height"],
            "inliers": inliers,
            "match_confidence": confidence,
            "patch_bbox_norm": bbox_norm,
            "upscale_factor": round(float(upscale_factor), 2),
        }


document_stitch_service = DocumentStitchService()
