from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import io
import json
import math
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple
import anyio
from fastapi import UploadFile, status
import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import session_scope
from app.core.exceptions import AppError, BadRequestError, NotFoundError
from app.core.logging_config import logger
from app.core.network import build_qr_png, resolve_effective_base_url
from app.models.audit import AuditLog
from app.models.item import Item
from app.models.location import Location
from app.models.session import LabelScanSessionRecord
from app.repositories.location_repository import location_repository
from app.schemas.label_scan import (
    CellLabelResult,
    CloseupZone,
    LabelScanAnalyzeRequest,
    LabelScanAnalyzeResponse,
    LabelScanApplyRequest,
    LabelScanApplyResponse,
    LabelScanCloseupRequest,
    LabelScanSessionStatusResponse,
    LabelScanSummary,
)
from app.schemas.location import GridSlotEnsureRequest
from app.services.location_service import _GRID_SLOT_LOCK, location_service
from app.services.photo_service import photo_service
from app.services.vision_service import _extract_json_object, _validate_model_name, vision_service

_GENERIC_NAME_RE = re.compile(r"^(drawer|bin|shelf|compartment|cubby|slot)\s+\d+$", re.IGNORECASE)
_COORD_NAME_RE = re.compile(r"^[A-Z]{1,2}\d+(?:[\u2013\u2014\-][A-Z]{1,2}\d+)?$", re.IGNORECASE)
_DIGIT_TOKEN_RE = re.compile(r"[a-zA-Z]*\d+[a-zA-Z0-9.\-]*")

_OCR_LOCK = threading.Lock()
_RAPIDOCR_INSTANCE: Any = None
_RAPIDOCR_CHECKED = False


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _db_ts(db: Session, dt: datetime) -> datetime:
    utc_dt = _as_utc(dt)
    if db.get_bind().dialect.name == "sqlite":
        return utc_dt.replace(tzinfo=None)
    return utc_dt


def _col_to_letter(col: int) -> str:
    return chr(64 + col) if 1 <= col <= 26 else str(col)


def format_slot_coord(row: int, col: int, row_span: int = 1, col_span: int = 1) -> str:
    start = f"{_col_to_letter(col)}{row}"
    if row_span <= 1 and col_span <= 1:
        return start
    end = f"{_col_to_letter(col + col_span - 1)}{row + row_span - 1}"
    return f"{start}\u2013{end}"


def is_custom_slot_name(name: Optional[str]) -> bool:
    cleaned = (name or "").strip()
    if not cleaned:
        return False
    if _GENERIC_NAME_RE.match(cleaned) or _COORD_NAME_RE.match(cleaned):
        return False
    return True


def _split_label_text(raw_text: str, proposed_short: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Split multi-line sticker text into a concise compartment name (<=120) and optional notes."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]

    short = re.sub(r"\s+", " ", (proposed_short or "")).strip()
    if short:
        name = short[:120].strip()
        joined = "\n".join(lines)
        if joined and joined.lower() != name.lower() and len(joined) > len(name) + 3:
            return name, joined[:2000]
        return name, None

    if not lines:
        return "", None

    if len(lines) == 1:
        single = lines[0]
        if len(single) <= 120:
            return single, None
        cut = single[:117].rsplit(" ", 1)[0].strip() or single[:117]
        return cut, single[:2000]

    # Combine first 1-2 lines up to 75 chars for a readable grid card title, keep full text in notes.
    title = lines[0]
    if len(title) < 28 and len(lines) > 1 and len(title) + 1 + len(lines[1]) <= 75:
        title = f"{title} {lines[1]}"
    if len(title) > 120:
        title = title[:117].rsplit(" ", 1)[0].strip() or title[:117]
    full_notes = "\n".join(lines)[:2000]
    return title, (full_notes if full_notes.lower() != title.lower() else None)


def _extract_digit_tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _DIGIT_TOKEN_RE.findall(text or "")}


def _digits_disagree(text_a: str, text_b: str) -> bool:
    """Detect whether two readings disagree on alphanumeric/numeric part tokens (e.g. LM2596 vs LM2576)."""
    toks_a = _extract_digit_tokens(text_a)
    toks_b = _extract_digit_tokens(text_b)
    if not toks_a or not toks_b:
        return False
    return toks_a != toks_b


def compute_perspective_coeffs(
    dst_pts: Sequence[Tuple[float, float]],
    src_pts: Sequence[Tuple[float, float]],
) -> Tuple[float, ...]:
    """Solve the 8-parameter perspective transform mapping dst (x, y) -> src (X, Y) for Pillow."""
    if len(dst_pts) != 4 or len(src_pts) != 4:
        raise BadRequestError("Exactly 4 corner points are required.")

    matrix: List[List[float]] = []
    rhs: List[float] = []
    for (x, y), (X, Y) in zip(dst_pts, src_pts, strict=True):
        matrix.append([x, y, 1.0, 0.0, 0.0, 0.0, -X * x, -X * y])
        rhs.append(X)
        matrix.append([0.0, 0.0, 0.0, x, y, 1.0, -Y * x, -Y * y])
        rhs.append(Y)

    n = 8
    aug = [matrix[r][:] + [rhs[r]] for r in range(n)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-8:
            raise BadRequestError("The 4 corners cannot be collinear or collapsed. Spread them across the container.")
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= pivot
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                factor = aug[r][col]
                for j in range(col, n + 1):
                    aug[r][j] -= factor * aug[col][j]

    return tuple(aug[i][n] for i in range(n))


def rectify_quad(
    img: Image.Image,
    quad_norm: Sequence[Tuple[float, float]],
    target_w: int,
    target_h: int,
) -> Image.Image:
    """Warp the 4 normalized (0..1) quad corners [TL, TR, BR, BL] into a flat target_w x target_h image."""
    w, h = img.size
    if w < 16 or h < 16:
        raise BadRequestError("Source image is too small to analyze.")
    src_pts = [(float(pt[0]) * w, float(pt[1]) * h) for pt in quad_norm]

    xs = [p[0] for p in src_pts]
    ys = [p[1] for p in src_pts]
    if (max(xs) - min(xs)) < 24.0 or (max(ys) - min(ys)) < 24.0:
        raise BadRequestError("Selected corner region is too small.")

    # Shoelace area check to reject crossed/twisted or degenerate polygons
    area2 = 0.0
    for i in range(4):
        x1, y1 = src_pts[i]
        x2, y2 = src_pts[(i + 1) % 4]
        area2 += (x1 * y2) - (x2 * y1)
    if abs(area2) * 0.5 < 400.0:
        raise BadRequestError("Selected 4 corners form an invalid or twisted polygon.")

    out_w = max(64, min(4096, int(target_w)))
    out_h = max(64, min(4096, int(target_h)))
    dst_pts = [(0.0, 0.0), (float(out_w), 0.0), (float(out_w), float(out_h)), (0.0, float(out_h))]
    coeffs = compute_perspective_coeffs(dst_pts, src_pts)
    return img.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coeffs, resample=Image.Resampling.BICUBIC)


def assess_cell_quality(crop: Image.Image) -> Tuple[bool, bool, float, Optional[str]]:
    """Evaluate a cropped compartment image using objective size, contrast, glare, and edge sharpness signals.

    Returns (is_usable, has_sticker, quality_score_0_to_1, failure_reason).
    """
    w, h = crop.size
    gray = crop.convert("L")
    if w >= 30 and h >= 24:
        mx = max(2, int(w * 0.08))
        my = max(2, int(h * 0.08))
        inner = gray.crop((mx, my, w - mx, h - my))
    else:
        inner = gray

    inner_stat = ImageStat.Stat(inner)
    contrast_std = float(inner_stat.stddev[0]) if inner_stat.stddev else 0.0

    edges = inner.filter(ImageFilter.FIND_EDGES)
    edge_stat = ImageStat.Stat(edges)
    edge_std = float(edge_stat.stddev[0]) if edge_stat.stddev else 0.0

    hist = inner.histogram()
    total_px = max(1, sum(hist))
    bright_px = sum(hist[185:])
    glare_px = sum(hist[252:])
    bright_frac = bright_px / total_px
    glare_ratio = glare_px / total_px

    has_sticker = bool(bright_frac >= 0.07 or contrast_std >= 16.0 or edge_std >= 13.0)
    if not has_sticker:
        return False, False, 0.0, None

    if w < 64 or h < 36:
        return False, True, 0.2, "Too small in photo — close-up needed"

    if glare_ratio > 0.72 and edge_std < 18.0:
        return False, True, 0.25, "Glare obscures label — close-up needed"

    if edge_std < 14.5:
        return False, True, max(0.15, min(0.45, edge_std / 32.0)), "Label is too blurry — close-up needed"

    quality_score = min(1.0, max(0.35, edge_std / 42.0))
    return True, True, quality_score, None


def _get_rapidocr() -> Any:
    """Lazy-load RapidOCR once if installed; return None cleanly when absent."""
    global _RAPIDOCR_INSTANCE, _RAPIDOCR_CHECKED
    if _RAPIDOCR_CHECKED:
        return _RAPIDOCR_INSTANCE
    with _OCR_LOCK:
        if _RAPIDOCR_CHECKED:
            return _RAPIDOCR_INSTANCE
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            _RAPIDOCR_INSTANCE = RapidOCR()
        except Exception:
            _RAPIDOCR_INSTANCE = None
        _RAPIDOCR_CHECKED = True
        return _RAPIDOCR_INSTANCE


def _run_rapidocr_on_crop(crop: Image.Image) -> Optional[Tuple[str, float]]:
    """Run local RapidOCR on a single PIL crop. Returns (joined_text, avg_confidence_0_to_1) or None."""
    engine = _get_rapidocr()
    if engine is None:
        return None
    try:
        import numpy as np  # type: ignore

        rgb = crop.convert("RGB")
        # Upscale small crops 2x so tiny 6pt sticker text is easier for the detector.
        if rgb.width < 260:
            scale = min(3.0, 260.0 / max(1, rgb.width))
            rgb = rgb.resize(
                (max(1, int(round(rgb.width * scale))), max(1, int(round(rgb.height * scale)))),
                Image.Resampling.LANCZOS,
            )
        arr = np.asarray(rgb)
        result, _ = engine(arr)
        if not result:
            return "", 0.0
        lines: List[str] = []
        confs: List[float] = []
        for entry in result:
            if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                continue
            txt = str(entry[1] or "").strip()
            try:
                score = float(entry[2])
            except (TypeError, ValueError):
                score = 0.0
            if txt:
                lines.append(txt)
                confs.append(max(0.0, min(1.0, score)))
        if not lines:
            return "", 0.0
        avg_conf = sum(confs) / len(confs)
        return "\n".join(lines), avg_conf
    except Exception as exc:
        logger.debug("RapidOCR failed on crop: %s", exc)
        return None


def _run_rapidocr_tiled(
    rectified: Image.Image,
    cell_rects: Sequence[Tuple[str, int, int, int, int]],
    row_bounds: Sequence[float],
    col_bounds: Sequence[float],
    sub_rows: int,
    sub_cols: int,
) -> Dict[str, Tuple[str, float]]:
    """Run RapidOCR on up to 4 grid-aligned quadrant tiles and map detections back to cell coords."""
    engine = _get_rapidocr()
    if engine is None or not cell_rects:
        return {}
    try:
        import numpy as np  # type: ignore

        w, h = rectified.size
        r_splits = [(0, sub_rows)] if sub_rows < 4 else [(0, sub_rows // 2), (sub_rows // 2, sub_rows)]
        c_splits = [(0, sub_cols)] if sub_cols < 4 else [(0, sub_cols // 2), (sub_cols // 2, sub_cols)]

        hits_by_coord: Dict[str, List[Tuple[float, str, float]]] = {coord: [] for coord, *_ in cell_rects}

        for r0, r1 in r_splits:
            for c0, c1 in c_splits:
                tx0 = max(0, int(round(col_bounds[c0] * w)))
                ty0 = max(0, int(round(row_bounds[r0] * h)))
                tx1 = min(w, int(round(col_bounds[c1] * w)))
                ty1 = min(h, int(round(row_bounds[r1] * h)))
                if tx1 - tx0 < 16 or ty1 - ty0 < 16:
                    continue
                tile = rectified.crop((tx0, ty0, tx1, ty1)).convert("RGB")
                scale = 1.0
                if tile.width < 960:
                    scale = min(2.0, 960.0 / max(1, tile.width))
                    tile = tile.resize(
                        (max(1, int(round(tile.width * scale))), max(1, int(round(tile.height * scale)))),
                        Image.Resampling.LANCZOS,
                    )
                res, _ = engine(np.asarray(tile))
                if not res:
                    continue
                for entry in res:
                    if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                        continue
                    box, raw_txt, raw_score = entry[0], str(entry[1] or "").strip(), entry[2]
                    if not raw_txt or not isinstance(box, (list, tuple)) or len(box) < 4:
                        continue
                    try:
                        score = max(0.0, min(1.0, float(raw_score)))
                        cx = sum(float(p[0]) for p in box) / len(box)
                        cy = sum(float(p[1]) for p in box) / len(box)
                    except (TypeError, ValueError, IndexError):
                        continue
                    gx = tx0 + (cx / scale)
                    gy = ty0 + (cy / scale)
                    for coord, cx0, cy0, cx1, cy1 in cell_rects:
                        if cx0 <= gx <= cx1 and cy0 <= gy <= cy1:
                            hits_by_coord[coord].append((gy, raw_txt, score))
                            break

        mapped: Dict[str, Tuple[str, float]] = {}
        for coord, items in hits_by_coord.items():
            if not items:
                mapped[coord] = ("", 0.0)
            else:
                items.sort(key=lambda it: it[0])
                text = "\n".join(it[1] for it in items)
                avg = sum(it[2] for it in items) / len(items)
                mapped[coord] = (text, avg)
        return mapped
    except Exception as exc:
        logger.debug("Tiled RapidOCR failed: %s", exc)
        return {}


def _build_contact_sheet(cells: Sequence[Tuple[str, Image.Image]], cols: int = 2) -> bytes:
    """Arrange labeled compartment crops into a crisp Set-of-Mark JPEG contact sheet."""
    if not cells:
        raise ValueError("Cannot build contact sheet for zero cells.")
    cols = max(1, min(cols, len(cells)))
    rows = math.ceil(len(cells) / cols)
    tile_w = 340
    tile_h = 220
    header_h = 28
    pad = 10
    sheet_w = cols * (tile_w + pad) + pad
    sheet_h = rows * (tile_h + header_h + pad) + pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), (24, 28, 36))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    for idx, (coord, crop) in enumerate(cells):
        r = idx // cols
        c = idx % cols
        x0 = pad + c * (tile_w + pad)
        y0 = pad + r * (tile_h + header_h + pad)

        draw.rectangle([x0, y0, x0 + tile_w, y0 + header_h], fill=(14, 165, 233))
        draw.text((x0 + 8, y0 + 7), f"[{coord}]", fill=(255, 255, 255), font=font)

        fit = crop.convert("RGB")
        fit.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
        ox = x0 + (tile_w - fit.width) // 2
        oy = y0 + header_h + (tile_h - fit.height) // 2
        draw.rectangle([x0, y0 + header_h, x0 + tile_w, y0 + header_h + tile_h], fill=(15, 23, 42))
        sheet.paste(fit, (ox, oy))
        draw.rectangle([x0, y0, x0 + tile_w, y0 + header_h + tile_h], outline=(100, 116, 139), width=1)

    buf = io.BytesIO()
    sheet.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


_LABEL_BATCH_PROMPT = (
    "You are reading printed or handwritten drawer/bin labels from a contact sheet of cropped organizer compartments. "
    "Each tile is labeled at the top with its exact coordinate in square brackets, like [A1], [B2]. "
    "CRITICAL ANTI-GUESSING RULE: Read ONLY what is clearly visible on each sticker. "
    "NEVER guess, invent, or autocomplete blurry digits, part numbers (such as HC-SR04 vs HC-SR04P, LM2596 vs LM2576), or words. "
    "For EACH tile coordinate shown, return an entry in \"labels\":\n"
    "- status: \"confident\" (all text is clearly readable), \"uncertain\" (mostly readable, e.g. handwritten or 1-2 questionable characters), "
    "\"unreadable\" (a sticker is present but too blurry, tiny, cut off, or glared to read reliably), or \"unlabeled\" (blank drawer / no sticker).\n"
    "- proposed_name: concise drawer title (<=80 chars). MUST be \"\" (empty string) whenever status is \"unreadable\" or \"unlabeled\".\n"
    "- extra_notes: secondary lines/specs if the sticker has multiple lines, else \"\".\n"
    "- raw_text: exact transcribed lines separated by \\n (or \"\" if unreadable/unlabeled).\n"
    "- reason: brief reason if uncertain or unreadable (e.g. \"blurry text\", \"handwritten\", \"glare\"), else \"\".\n"
    'Return ONLY valid JSON: {"labels": [{"coord": "A1", "status": "confident", "proposed_name": "...", "extra_notes": "...", "raw_text": "...", "reason": ""}]}'
)


async def _call_vision_label_batch(
    cells: Sequence[Tuple[str, Image.Image]],
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[Dict[str, Dict[str, Any]], str]:
    """Send a small contact sheet (<=6 tiles) to the configured vision provider and parse per-coord reads."""
    if not cells:
        return {}, "none"

    sheet_bytes = await anyio.to_thread.run_sync(_build_contact_sheet, cells, 2)
    b64_data = base64.b64encode(sheet_bytes).decode("utf-8")

    selected = (provider or settings.VISION_PROVIDER or "").strip().lower()
    gemini_key = vision_service._effective_gemini_key()
    openai_key = vision_service._effective_openai_key()
    if not selected or selected == "auto":
        selected = "gemini" if gemini_key else ("openai" if openai_key else "local")

    parsed: Dict[str, Any] = {}
    engine_label = selected
    try:
        if selected == "gemini":
            if not gemini_key:
                return {}, "unavailable"
            safe_model = _validate_model_name(model or settings.GEMINI_VISION_MODEL, "gemini")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{safe_model}:generateContent"
            payload = vision_service._build_gemini_payload(_LABEL_BATCH_PROMPT, b64_data, "image/jpeg")
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload, headers={"x-goog-api-key": gemini_key})
            if resp.status_code != 200:
                return {}, "fallback"
            raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json_object(raw_text)
            engine_label = f"gemini:{safe_model}"
        elif selected == "openai":
            if not openai_key:
                return {}, "unavailable"
            safe_model = _validate_model_name(model or settings.OPENAI_VISION_MODEL, "openai")
            payload = vision_service._build_openai_payload(_LABEL_BATCH_PROMPT, safe_model, b64_data, "image/jpeg")
            headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            if resp.status_code != 200:
                return {}, "fallback"
            raw_text = resp.json()["choices"][0]["message"]["content"]
            parsed = _extract_json_object(raw_text)
            engine_label = f"openai:{safe_model}"
        else:
            safe_model = _validate_model_name(vision_service._effective_ollama_model(model), "local")
            ollama_url = vision_service._effective_ollama_url()
            payload = {
                "model": safe_model,
                "messages": [{"role": "user", "content": _LABEL_BATCH_PROMPT, "images": [b64_data]}],
                "format": "json",
                "stream": False,
                "options": {"num_predict": 2048, "temperature": 0.1},
            }
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(f"{ollama_url}/api/chat", json=payload)
            if resp.status_code != 200:
                return {}, "fallback"
            resp_data = resp.json()
            raw_text = (resp_data.get("message") or {}).get("content") or resp_data.get("response") or ""
            parsed = _extract_json_object(raw_text)
            engine_label = f"ollama:{safe_model}"
    except BadRequestError:
        raise
    except (httpx.RequestError, httpx.TimeoutException, ConnectionError, OSError) as exc:
        logger.warning("Vision label batch failed (%s): %s", selected, exc)
        return {}, "connection_failed"
    except Exception as exc:
        logger.warning("Vision label batch failed (%s): %s", selected, exc)
        return {}, "fallback"

    by_coord: Dict[str, Dict[str, Any]] = {}
    for item in parsed.get("labels") or []:
        if not isinstance(item, dict):
            continue
        coord_key = str(item.get("coord") or "").strip().strip("[]").upper()
        if coord_key:
            by_coord[coord_key] = item
    return by_coord, engine_label


def _resolve_cell_outcome(
    *,
    is_usable: bool,
    has_sticker: bool,
    quality_score: float,
    quality_reason: Optional[str],
    crop_width: int,
    ocr_result: Optional[Tuple[str, float]],
    vlm_entry: Optional[Dict[str, Any]],
) -> Tuple[str, Optional[str], str, int, str, Optional[str], str]:
    """Compute final (proposed_name, proposed_description, raw_text, confidence, tier, reason, source)."""
    ocr_text, ocr_conf_f = ocr_result if ocr_result is not None else ("", 0.0)
    ocr_conf = int(round(max(0.0, min(1.0, ocr_conf_f)) * 100))

    vlm_status = str((vlm_entry or {}).get("status") or "").strip().lower()
    vlm_raw = str((vlm_entry or {}).get("raw_text") or "").strip()
    vlm_name = str((vlm_entry or {}).get("proposed_name") or "").strip()
    vlm_notes = str((vlm_entry or {}).get("extra_notes") or "").strip() or None
    vlm_reason = str((vlm_entry or {}).get("reason") or "").strip() or None

    if not has_sticker and not ocr_text and vlm_status in ("", "unlabeled"):
        return "", None, "", 0, "unlabeled", None, "empty"

    if not is_usable:
        return (
            "",
            None,
            "",
            min(38, max(5, int(round(quality_score * 40)))),
            "red",
            quality_reason or "Close-up needed",
            "quality_gate",
        )

    # Case 1: RapidOCR produced output
    if ocr_result is not None:
        if not ocr_text:
            if vlm_entry and vlm_status in ("confident", "uncertain") and (vlm_name or vlm_raw):
                name, desc = _split_label_text(vlm_raw or vlm_name, vlm_name)
                if not desc and vlm_notes:
                    desc = vlm_notes[:2000]
                return (
                    name,
                    desc,
                    (vlm_raw or name)[:2000],
                    72,
                    "yellow",
                    vlm_reason or "Handwritten or stylized label — please verify",
                    "hybrid",
                )
            if vlm_status == "unlabeled" and not has_sticker:
                return "", None, "", 0, "unlabeled", None, "empty"
            return (
                "",
                None,
                "",
                25,
                "red",
                vlm_reason or "Text could not be read reliably — close-up needed",
                "ocr",
            )

        # OCR has text
        vlm_compare_text = vlm_raw or vlm_name
        if vlm_entry and vlm_compare_text and _digits_disagree(ocr_text, vlm_compare_text):
            name, desc = _split_label_text(ocr_text, None)
            return (
                name,
                desc,
                ocr_text[:2000],
                min(74, max(60, ocr_conf)),
                "yellow",
                "OCR and AI disagree on digits/part code — please double check",
                "hybrid",
            )

        if ocr_conf >= 85:
            short_hint = vlm_name if (vlm_entry and vlm_status == "confident" and not _digits_disagree(ocr_text, vlm_name)) else None
            name, desc = _split_label_text(ocr_text, short_hint)
            if not desc and vlm_notes and not _digits_disagree(ocr_text, vlm_notes):
                desc = vlm_notes[:2000]
            return name, desc, ocr_text[:2000], ocr_conf, "green", None, ("hybrid" if short_hint else "ocr")

        if ocr_conf >= 60:
            name, desc = _split_label_text(ocr_text, vlm_name if vlm_status == "confident" else None)
            return (
                name,
                desc,
                ocr_text[:2000],
                ocr_conf,
                "yellow",
                "Moderate OCR confidence — please verify",
                "ocr",
            )

        # ocr_conf < 60: check if VLM rescued a handwritten/stylized label cleanly
        if vlm_entry and vlm_status in ("confident", "uncertain") and (vlm_name or vlm_raw):
            name, desc = _split_label_text(vlm_raw or vlm_name, vlm_name)
            return (
                name,
                desc,
                (vlm_raw or ocr_text)[:2000],
                68,
                "yellow",
                vlm_reason or "Low OCR confidence — AI draft needs verification",
                "hybrid",
            )
        return (
            "",
            None,
            "",
            max(10, ocr_conf),
            "red",
            "Low OCR confidence — close-up needed",
            "ocr",
        )

    # Case 2: RapidOCR absent — Vision model only (backed by objective quality gate)
    if vlm_entry is None:
        return (
            "",
            None,
            "",
            20,
            "red",
            "No OCR/AI reading available — close-up needed",
            "quality_gate",
        )

    if vlm_status == "unlabeled":
        return "", None, "", 0, "unlabeled", None, "empty"

    if vlm_status == "unreadable" or (not vlm_name and not vlm_raw):
        return (
            "",
            None,
            "",
            25,
            "red",
            vlm_reason or "Label unreadable — close-up needed",
            "ollama",
        )

    name, desc = _split_label_text(vlm_raw or vlm_name, vlm_name)
    if not desc and vlm_notes:
        desc = vlm_notes[:2000]

    if vlm_status == "confident" and quality_score >= 0.62 and crop_width >= 140:
        return name, desc, (vlm_raw or name)[:2000], 88, "green", None, "ollama"

    return (
        name,
        desc,
        (vlm_raw or name)[:2000],
        74 if vlm_status == "confident" else 68,
        "yellow",
        vlm_reason or "Verify label against sticker crop",
        "ollama",
    )


def cluster_closeup_zones(
    results: Sequence[CellLabelResult],
    grid_rows: int,
    grid_cols: int,
    max_rows: int = 3,
    max_cols: int = 4,
) -> List[CloseupZone]:
    """Group 'red' cells into compact non-overlapping rectangular close-up zones."""
    red_cells = sorted(
        [r for r in results if r.confidence_tier == "red"],
        key=lambda r: (r.grid_row, r.grid_col),
    )
    if not red_cells:
        return []

    remaining = list(red_cells)
    zones: List[CloseupZone] = []

    while remaining:
        seed = remaining[0]
        min_r = seed.grid_row
        max_r = seed.grid_row + max(1, seed.grid_row_span) - 1
        min_c = seed.grid_col
        max_c = seed.grid_col + max(1, seed.grid_col_span) - 1

        changed = True
        while changed:
            changed = False
            for cell in remaining:
                c_r0 = cell.grid_row
                c_r1 = cell.grid_row + max(1, cell.grid_row_span) - 1
                c_c0 = cell.grid_col
                c_c1 = cell.grid_col + max(1, cell.grid_col_span) - 1
                cand_min_r = min(min_r, c_r0)
                cand_max_r = max(max_r, c_r1)
                cand_min_c = min(min_c, c_c0)
                cand_max_c = max(max_c, c_c1)
                if (cand_max_r - cand_min_r + 1) <= max_rows and (cand_max_c - cand_min_c + 1) <= max_cols:
                    if (cand_min_r, cand_max_r, cand_min_c, cand_max_c) != (min_r, max_r, min_c, max_c):
                        min_r, max_r, min_c, max_c = cand_min_r, cand_max_r, cand_min_c, cand_max_c
                        changed = True

        covered: List[CellLabelResult] = []
        next_remaining: List[CellLabelResult] = []
        for cell in remaining:
            c_r0 = cell.grid_row
            c_r1 = cell.grid_row + max(1, cell.grid_row_span) - 1
            c_c0 = cell.grid_col
            c_c1 = cell.grid_col + max(1, cell.grid_col_span) - 1
            if c_r0 >= min_r and c_r1 <= max_r and c_c0 >= min_c and c_c1 <= max_c:
                covered.append(cell)
            else:
                next_remaining.append(cell)

        start_lbl = format_slot_coord(min_r, min_c)
        end_lbl = format_slot_coord(max_r, max_c)
        label = start_lbl if start_lbl == end_lbl else f"{start_lbl}\u2013{end_lbl}"
        zones.append(
            CloseupZone(
                zone_id=f"zone_{min_r}_{min_c}_{max_r}_{max_c}",
                label=label,
                min_row=min_r,
                max_row=max_r,
                min_col=min_c,
                max_col=max_c,
                grid_rows=grid_rows,
                grid_cols=grid_cols,
                slot_coords=[c.coord for c in covered],
                slot_count=len(covered),
            )
        )
        remaining = next_remaining

    return zones


def compute_summary(results: Sequence[CellLabelResult]) -> LabelScanSummary:
    counts = {"green": 0, "yellow": 0, "red": 0, "unlabeled": 0}
    for r in results:
        if r.confidence_tier in counts:
            counts[r.confidence_tier] += 1
    return LabelScanSummary(
        total=len(results),
        green=counts["green"],
        yellow=counts["yellow"],
        red=counts["red"],
        unlabeled=counts["unlabeled"],
    )


@dataclass
class _PlannedCell:
    coord: str
    slot_id: Optional[int]
    grid_row: int
    grid_col: int
    grid_row_span: int
    grid_col_span: int
    existing_name: Optional[str]
    has_custom_existing_name: bool


def _validate_axis_boundaries(raw: Optional[List[float]], count: int, axis_name: str) -> List[float]:
    if raw is None:
        return [i / float(count) for i in range(count + 1)]
    if len(raw) != count + 1:
        raise BadRequestError(f"{axis_name} must have {count + 1} values for {count} slots.")
    bounds = [float(v) for v in raw]
    if abs(bounds[0]) > 0.01 or abs(bounds[-1] - 1.0) > 0.01:
        raise BadRequestError(f"{axis_name} must start at 0.0 and end at 1.0.")
    bounds[0] = 0.0
    bounds[-1] = 1.0
    for idx in range(len(bounds) - 1):
        if bounds[idx + 1] - bounds[idx] < 0.005:
            raise BadRequestError(f"{axis_name} must be strictly increasing.")
    return bounds


class LabelScanService:
    """Database-backed coordinator for container auto-labeling QR sessions."""

    TTL_SECONDS = 1800
    MAX_SESSIONS = 500
    MAX_CELLS = 256

    @staticmethod
    def _prune_expired(db: Session, now: datetime, include_recently_expired: bool = False) -> None:
        cutoff = now if include_recently_expired else (now - timedelta(minutes=30))
        db.execute(delete(LabelScanSessionRecord).where(LabelScanSessionRecord.expires_at < _db_ts(db, cutoff)))

    def create_session(
        self,
        db: Session,
        container_id: int,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> dict:
        container = location_repository.get_by_id(db, container_id)
        if not container:
            raise NotFoundError("Container not found.")

        now = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(24)

        self._prune_expired(db, now)
        active_count = int(db.execute(select(func.count(LabelScanSessionRecord.token))).scalar() or 0)
        if active_count >= self.MAX_SESSIONS:
            self._prune_expired(db, now, include_recently_expired=True)
            active_count = int(db.execute(select(func.count(LabelScanSessionRecord.token))).scalar() or 0)
        if active_count >= self.MAX_SESSIONS:
            raise AppError(
                "Too many active label scan sessions. Please wait and try again.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_code="too_many_sessions",
            )

        rec = LabelScanSessionRecord(
            token=token,
            container_id=container.id,
            container_name=container.name[:120],
            status="waiting_for_photo",
            overview_photo_path=None,
            active_zone_json=None,
            closeup_photos_json="[]",
            results_json="[]",
            created_at=_db_ts(db, now),
            expires_at=_db_ts(db, now + timedelta(seconds=self.TTL_SECONDS)),
        )
        db.add(rec)
        db.flush()

        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        mobile_url = f"{effective_base}/label-capture.html?token={token}"
        return {
            "token": token,
            "container_id": container.id,
            "container_name": container.name,
            "status": "waiting_for_photo",
            "expires_in_seconds": self.TTL_SECONDS,
            "mobile_url": mobile_url,
            "qr_url": f"/api/v1/label-scan/sessions/{token}/qr",
        }

    def _get_record_or_404(self, db: Session, token: str) -> LabelScanSessionRecord:
        rec = db.get(LabelScanSessionRecord, token)
        if not rec:
            raise NotFoundError("Label scan session not found.")
        if datetime.now(timezone.utc) > _as_utc(rec.expires_at) and rec.status != "completed":
            rec.status = "expired"
            db.flush()
        return rec

    def _require_active_record(self, db: Session, token: str) -> LabelScanSessionRecord:
        rec = self._get_record_or_404(db, token)
        if rec.status == "expired" or (
            rec.status != "completed" and datetime.now(timezone.utc) > _as_utc(rec.expires_at)
        ):
            raise AppError(
                "Label scan session has expired. Please generate a new QR code.",
                status_code=status.HTTP_410_GONE,
                error_code="session_expired",
            )
        return rec

    @staticmethod
    def _parse_json_list(raw: Optional[str]) -> List[Any]:
        try:
            data = json.loads(raw or "[]")
            return list(data) if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    @staticmethod
    def _parse_zone(raw: Optional[str]) -> Optional[CloseupZone]:
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return CloseupZone.model_validate(data) if isinstance(data, dict) else None
        except Exception:
            return None

    def get_session_status(self, db: Session, token: str) -> LabelScanSessionStatusResponse:
        rec = self._get_record_or_404(db, token)
        results = [CellLabelResult.model_validate(item) for item in self._parse_json_list(rec.results_json)]
        closeup_photos = [dict(p) for p in self._parse_json_list(rec.closeup_photos_json) if isinstance(p, dict)]
        container = location_repository.get_by_id(db, rec.container_id)
        grid_rows = int(container.grid_rows or 1) if container and container.grid_rows else 1
        grid_cols = int(container.grid_cols or 1) if container and container.grid_cols else 1
        if results:
            grid_rows = max(grid_rows, max(r.grid_row + r.grid_row_span - 1 for r in results))
            grid_cols = max(grid_cols, max(r.grid_col + r.grid_col_span - 1 for r in results))
        zones = cluster_closeup_zones(results, grid_rows, grid_cols) if results else []
        remaining_seconds = max(0, int((_as_utc(rec.expires_at) - datetime.now(timezone.utc)).total_seconds()))
        return LabelScanSessionStatusResponse(
            token=rec.token,
            container_id=rec.container_id,
            container_name=rec.container_name,
            status=rec.status,
            expires_in_seconds=remaining_seconds,
            overview_photo_path=rec.overview_photo_path,
            active_zone=self._parse_zone(rec.active_zone_json),
            closeup_photos=closeup_photos,
            results=results,
            closeup_zones=zones,
            summary=compute_summary(results),
        )

    def generate_qr_png(
        self,
        db: Session,
        token: str,
        host_header: Optional[str] = None,
        base_url_param: Optional[str] = None,
    ) -> bytes:
        self._require_active_record(db, token)
        effective_base = resolve_effective_base_url(host_header=host_header, base_url_param=base_url_param)
        mobile_url = f"{effective_base}/label-capture.html?token={token}"
        return build_qr_png(mobile_url)

    def process_upload(
        self,
        db: Session,
        token: str,
        file: UploadFile,
        zone_id: Optional[str] = None,
    ) -> dict:
        rec = self._require_active_record(db, token)
        result = photo_service.process_and_save(
            file,
            max_dimension=settings.MAX_LABEL_SCAN_DIMENSION,
            jpeg_quality=88,
        )
        photo_service.schedule_rollback_cleanup(db, result["original_path"], result.get("thumbnail_path"))

        now = datetime.now(timezone.utc)
        active_zone = self._parse_zone(rec.active_zone_json)
        effective_zone_id = (zone_id or (active_zone.zone_id if active_zone else "")).strip() or None

        if rec.overview_photo_path is None and not effective_zone_id:
            rec.overview_photo_path = result["original_path"]
            rec.status = "overview_uploaded"
        elif effective_zone_id:
            closeups = [dict(p) for p in self._parse_json_list(rec.closeup_photos_json) if isinstance(p, dict)]
            closeups.append(
                {
                    "zone_id": effective_zone_id,
                    "original_path": result["original_path"],
                    "thumbnail_path": result.get("thumbnail_path"),
                    "uploaded_at": now.isoformat(),
                }
            )
            rec.closeup_photos_json = json.dumps(closeups)
            if active_zone and active_zone.zone_id == effective_zone_id:
                active_zone.photo_path = result["original_path"]
                rec.active_zone_json = active_zone.model_dump_json()
            rec.status = "closeup_uploaded"
        else:
            rec.overview_photo_path = result["original_path"]
            rec.status = "overview_uploaded"

        rec.expires_at = _db_ts(db, now + timedelta(seconds=self.TTL_SECONDS))
        db.flush()
        return result

    def request_closeup(
        self,
        db: Session,
        token: str,
        data: LabelScanCloseupRequest,
    ) -> LabelScanSessionStatusResponse:
        rec = self._require_active_record(db, token)
        now = datetime.now(timezone.utc)
        if data.clear:
            rec.active_zone_json = None
            if rec.results_json and rec.results_json != "[]":
                rec.status = "ready_for_review"
            rec.expires_at = _db_ts(db, now + timedelta(seconds=self.TTL_SECONDS))
            db.flush()
            return self.get_session_status(db, token)

        container = location_repository.get_by_id(db, rec.container_id)
        if not container:
            raise NotFoundError("Container not found.")
        results = [CellLabelResult.model_validate(item) for item in self._parse_json_list(rec.results_json)]
        grid_rows = int(container.grid_rows or 1) if container.grid_rows else 1
        grid_cols = int(container.grid_cols or 1) if container.grid_cols else 1
        if results:
            grid_rows = max(grid_rows, max(r.grid_row + r.grid_row_span - 1 for r in results))
            grid_cols = max(grid_cols, max(r.grid_col + r.grid_col_span - 1 for r in results))

        zones = cluster_closeup_zones(results, grid_rows, grid_cols) if results else []
        chosen: Optional[CloseupZone] = None
        if data.zone_id:
            chosen = next((z for z in zones if z.zone_id == data.zone_id), None)
        if chosen is None and data.min_row and data.max_row and data.min_col and data.max_col:
            min_r = min(data.min_row, data.max_row)
            max_r = max(data.min_row, data.max_row)
            min_c = min(data.min_col, data.max_col)
            max_c = max(data.min_col, data.max_col)
            start_lbl = format_slot_coord(min_r, min_c)
            end_lbl = format_slot_coord(max_r, max_c)
            coords = data.slot_coords or [
                format_slot_coord(r, c)
                for r in range(min_r, max_r + 1)
                for c in range(min_c, max_c + 1)
            ]
            chosen = CloseupZone(
                zone_id=data.zone_id or f"zone_{min_r}_{min_c}_{max_r}_{max_c}",
                label=start_lbl if start_lbl == end_lbl else f"{start_lbl}\u2013{end_lbl}",
                min_row=min_r,
                max_row=max_r,
                min_col=min_c,
                max_col=max_c,
                grid_rows=grid_rows,
                grid_cols=grid_cols,
                slot_coords=coords,
                slot_count=len(coords),
            )
        if chosen is None:
            raise BadRequestError("No close-up zone specified or no unreadable cells remain.")

        rec.active_zone_json = chosen.model_dump_json()
        rec.status = "awaiting_closeup"
        rec.expires_at = _db_ts(db, now + timedelta(seconds=self.TTL_SECONDS))
        db.flush()
        return self.get_session_status(db, token)

    def _plan_cells_for_container(
        self,
        container: Location,
        req: LabelScanAnalyzeRequest,
    ) -> Tuple[int, int, List[_PlannedCell], Tuple[int, int, int, int]]:
        children = list(container.children or [])
        placed = [c for c in children if c.grid_row is not None and c.grid_col is not None]

        if req.grid_rows and req.grid_cols:
            grid_rows, grid_cols = req.grid_rows, req.grid_cols
        elif container.grid_rows and container.grid_cols:
            grid_rows, grid_cols = int(container.grid_rows), int(container.grid_cols)
        elif children:
            grid_rows, grid_cols = min(26, len(children)), 1
        else:
            raise BadRequestError("Specify grid rows and columns before reading labels.")

        if grid_rows * grid_cols > self.MAX_CELLS:
            raise BadRequestError(f"Grid exceeds maximum of {self.MAX_CELLS} compartments per scan.")

        # Bounds for full container or close-up sub-rectangle
        sub_r0 = req.min_row if req.min_row is not None else 1
        sub_r1 = req.max_row if req.max_row is not None else grid_rows
        sub_c0 = req.min_col if req.min_col is not None else 1
        sub_c1 = req.max_col if req.max_col is not None else grid_cols
        if sub_r0 > sub_r1 or sub_c0 > sub_c1 or sub_r1 > grid_rows or sub_c1 > grid_cols:
            raise BadRequestError("Close-up zone coordinates exceed container grid dimensions.")

        # Map existing placed slots
        slot_by_origin: Dict[Tuple[int, int], Location] = {}
        occupied: set[Tuple[int, int]] = set()
        for ch in placed:
            r0 = int(ch.grid_row or 1)
            c0 = int(ch.grid_col or 1)
            rs = max(1, int(ch.grid_row_span or 1))
            cs = max(1, int(ch.grid_col_span or 1))
            if r0 <= grid_rows and c0 <= grid_cols:
                slot_by_origin[(r0, c0)] = ch
                for rr in range(r0, min(grid_rows, r0 + rs - 1) + 1):
                    for cc in range(c0, min(grid_cols, c0 + cs - 1) + 1):
                        occupied.add((rr, cc))

        # Unplaced children fallback when container has no grid layout yet
        unplaced_by_row: Dict[int, Location] = {}
        if not placed and grid_cols == 1:
            for idx, ch in enumerate(sorted(children, key=lambda c: c.id), start=1):
                if idx <= grid_rows:
                    unplaced_by_row[idx] = ch

        planned: List[_PlannedCell] = []
        for r in range(sub_r0, sub_r1 + 1):
            for c in range(sub_c0, sub_c1 + 1):
                if (r, c) in slot_by_origin:
                    ch = slot_by_origin[(r, c)]
                    rs = max(1, min(grid_rows - r + 1, int(ch.grid_row_span or 1)))
                    cs = max(1, min(grid_cols - c + 1, int(ch.grid_col_span or 1)))
                    planned.append(
                        _PlannedCell(
                            coord=format_slot_coord(r, c, rs, cs),
                            slot_id=ch.id,
                            grid_row=r,
                            grid_col=c,
                            grid_row_span=rs,
                            grid_col_span=cs,
                            existing_name=ch.name,
                            has_custom_existing_name=is_custom_slot_name(ch.name),
                        )
                    )
                elif (r, c) in occupied:
                    # Covered by a merged cell whose origin is elsewhere
                    continue
                else:
                    fallback_ch = unplaced_by_row.get(r)
                    planned.append(
                        _PlannedCell(
                            coord=format_slot_coord(r, c, 1, 1),
                            slot_id=fallback_ch.id if fallback_ch else None,
                            grid_row=r,
                            grid_col=c,
                            grid_row_span=1,
                            grid_col_span=1,
                            existing_name=fallback_ch.name if fallback_ch else None,
                            has_custom_existing_name=is_custom_slot_name(fallback_ch.name) if fallback_ch else False,
                        )
                    )

        return grid_rows, grid_cols, planned, (sub_r0, sub_r1, sub_c0, sub_c1)

    def _verify_session_photo_access(self, rec: LabelScanSessionRecord, photo_path: str) -> Path:
        allowed_paths: set[str] = set()
        if rec.overview_photo_path:
            allowed_paths.add(rec.overview_photo_path)
        for p in self._parse_json_list(rec.closeup_photos_json):
            if isinstance(p, dict) and p.get("original_path"):
                allowed_paths.add(str(p["original_path"]))
        if photo_path not in allowed_paths:
            raise BadRequestError("Photo does not belong to this label scan session.")
        disk_path = photo_service.resolve_safe_upload_path(photo_path)
        if not disk_path.exists() or not disk_path.is_file():
            raise NotFoundError("Uploaded scan photo file could not be found.")
        return disk_path

    async def analyze_session_photo(
        self,
        db: Session,
        token: str,
        req: LabelScanAnalyzeRequest,
    ) -> LabelScanAnalyzeResponse:
        bind = db.get_bind()

        def _load_scan_inputs() -> Tuple[Path, int, int, List[_PlannedCell], Tuple[int, int, int, int]]:
            with session_scope(bind) as thread_db:
                rec = self._require_active_record(thread_db, token)
                container = location_repository.get_by_id(thread_db, rec.container_id)
                if not container:
                    raise NotFoundError("Container not found.")
                disk_path = self._verify_session_photo_access(rec, req.photo_path)
                grid_rows, grid_cols, planned, sub_bounds = self._plan_cells_for_container(container, req)
                return disk_path, grid_rows, grid_cols, planned, sub_bounds

        disk_path, grid_rows, grid_cols, planned, (sub_r0, sub_r1, sub_c0, sub_c1) = (
            await anyio.to_thread.run_sync(_load_scan_inputs)
        )
        if not planned:
            raise BadRequestError("No grid compartments selected for analysis.")

        sub_rows = sub_r1 - sub_r0 + 1
        sub_cols = sub_c1 - sub_c0 + 1
        quad_pts = [(p.x, p.y) for p in req.quad]
        row_bounds = _validate_axis_boundaries(req.row_boundaries, sub_rows, "row_boundaries")
        col_bounds = _validate_axis_boundaries(req.col_boundaries, sub_cols, "col_boundaries")

        def _rectify_and_crop() -> List[Tuple[_PlannedCell, Image.Image, Tuple[bool, bool, float, Optional[str]], Optional[str], Optional[Tuple[str, float]]]]:
            with Image.open(disk_path) as opened:
                oriented = ImageOps.exif_transpose(opened).convert("RGB")
                # Estimate target dimensions proportional to the quad span on the source image.
                xs = [pt[0] * oriented.width for pt in quad_pts]
                ys = [pt[1] * oriented.height for pt in quad_pts]
                quad_w = max(120, int(round(max(xs) - min(xs))))
                quad_h = max(120, int(round(max(ys) - min(ys))))
                target_w = max(sub_cols * 140, min(4096, quad_w))
                target_h = max(sub_rows * 90, min(4096, quad_h))
                rectified = rectify_quad(oriented, quad_pts, target_w, target_h)

            raw_cells: List[Tuple[_PlannedCell, Image.Image, Tuple[bool, bool, float, Optional[str]], Optional[str], Tuple[int, int, int, int]]] = []
            usable_rects: List[Tuple[str, int, int, int, int]] = []
            for cell in planned:
                rel_r = cell.grid_row - sub_r0
                rel_c = cell.grid_col - sub_c0
                end_r = min(sub_rows, rel_r + cell.grid_row_span)
                end_c = min(sub_cols, rel_c + cell.grid_col_span)
                x0 = max(0, int(round(col_bounds[rel_c] * rectified.width)))
                y0 = max(0, int(round(row_bounds[rel_r] * rectified.height)))
                x1 = min(rectified.width, int(round(col_bounds[end_c] * rectified.width)))
                y1 = min(rectified.height, int(round(row_bounds[end_r] * rectified.height)))
                rect = (x0, y0, max(x0 + 12, x1), max(y0 + 12, y1))
                crop = rectified.crop(rect)
                quality = assess_cell_quality(crop)
                is_usable, has_sticker, _, _ = quality
                crop_url = photo_service.save_pil_crop(crop) if (has_sticker or is_usable) else None
                raw_cells.append((cell, crop, quality, crop_url, rect))
                if is_usable:
                    usable_rects.append((cell.coord, rect[0], rect[1], rect[2], rect[3]))

            tiled_ocr: Dict[str, Tuple[str, float]] = {}
            if len(usable_rects) > 6 and _get_rapidocr() is not None:
                tiled_ocr = _run_rapidocr_tiled(
                    rectified, usable_rects, row_bounds, col_bounds, sub_rows, sub_cols
                )

            prepared = []
            for cell, crop, quality, crop_url, _ in raw_cells:
                is_usable = quality[0]
                if not is_usable:
                    ocr_res = None
                elif tiled_ocr and cell.coord in tiled_ocr:
                    ocr_res = tiled_ocr[cell.coord]
                else:
                    ocr_res = _run_rapidocr_on_crop(crop)
                prepared.append((cell, crop, quality, crop_url, ocr_res))
            return prepared

        prepared_cells = await anyio.to_thread.run_sync(_rectify_and_crop)

        has_rapidocr = _get_rapidocr() is not None
        vlm_needed: List[Tuple[str, Image.Image]] = []
        for cell, crop, (is_usable, has_sticker, _, _), _, ocr_res in prepared_cells:
            if not is_usable or not has_sticker:
                continue
            if not has_rapidocr:
                vlm_needed.append((cell.coord, crop))
            elif req.use_ollama_refinement and ocr_res is not None:
                ocr_txt, ocr_conf = ocr_res
                if (not ocr_txt) or ocr_conf < 0.78:
                    vlm_needed.append((cell.coord, crop))

        if has_rapidocr and len(vlm_needed) > 27:
            vlm_needed = vlm_needed[:27]

        vlm_by_coord: Dict[str, Dict[str, Any]] = {}
        engine_parts: List[str] = ["rapidocr"] if has_rapidocr else []
        batch_size = 9 if has_rapidocr else 6
        for start_idx in range(0, len(vlm_needed), batch_size):
            batch = vlm_needed[start_idx : start_idx + batch_size]
            batch_map, batch_engine = await _call_vision_label_batch(
                batch, provider=req.provider, model=req.model
            )
            if batch_engine in ("connection_failed", "unavailable"):
                if not has_rapidocr:
                    raise AppError(
                        "Could not reach Ollama (or configured AI vision provider) and local RapidOCR is not installed. "
                        "Start Ollama (or install rapidocr-onnxruntime) and try again.",
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        error_code="vision_unavailable",
                    )
                # RapidOCR already ran; stop retrying unreachable Ollama for remaining batches
                break
            vlm_by_coord.update(batch_map)
            if batch_engine not in ("none", "fallback") and batch_engine not in engine_parts:
                engine_parts.append(batch_engine)

        new_results: List[CellLabelResult] = []
        for cell, crop, (is_usable, has_sticker, q_score, q_reason), crop_url, ocr_res in prepared_cells:
            vlm_entry = vlm_by_coord.get(cell.coord.upper())
            p_name, p_desc, raw_txt, conf, tier, reason, source = _resolve_cell_outcome(
                is_usable=is_usable,
                has_sticker=has_sticker,
                quality_score=q_score,
                quality_reason=q_reason,
                crop_width=crop.width,
                ocr_result=ocr_res,
                vlm_entry=vlm_entry,
            )
            new_results.append(
                CellLabelResult(
                    coord=cell.coord,
                    slot_id=cell.slot_id,
                    grid_row=cell.grid_row,
                    grid_col=cell.grid_col,
                    grid_row_span=cell.grid_row_span,
                    grid_col_span=cell.grid_col_span,
                    existing_name=cell.existing_name,
                    has_custom_existing_name=cell.has_custom_existing_name,
                    proposed_name=p_name,
                    proposed_description=p_desc,
                    raw_text=raw_txt,
                    confidence=conf,
                    confidence_tier=tier,
                    reason=reason,
                    crop_photo_path=crop_url,
                    source=source,  # type: ignore[arg-type]
                )
            )

        # Merge into existing session results if this was a close-up pass inside a short-lived DB scope
        is_subzone = (sub_r0, sub_r1, sub_c0, sub_c1) != (1, grid_rows, 1, grid_cols)

        def _persist_scan_results() -> Tuple[List[CellLabelResult], List[CloseupZone], LabelScanSummary]:
            with session_scope(bind) as thread_db:
                rec = self._require_active_record(thread_db, token)
                existing_results = [
                    CellLabelResult.model_validate(item) for item in self._parse_json_list(rec.results_json)
                ]
                if is_subzone and existing_results:
                    merged_by_key: Dict[Tuple[int, int], CellLabelResult] = {
                        (r.grid_row, r.grid_col): r for r in existing_results
                    }
                    tier_rank = {"red": 1, "unlabeled": 1, "yellow": 2, "green": 3}
                    for nr in new_results:
                        key = (nr.grid_row, nr.grid_col)
                        prev = merged_by_key.get(key)
                        if prev is None:
                            merged_by_key[key] = nr
                            continue
                        if prev.confidence_tier == "green" and tier_rank.get(nr.confidence_tier, 0) < 3:
                            continue
                        if tier_rank.get(nr.confidence_tier, 0) > tier_rank.get(prev.confidence_tier, 0) or (
                            nr.confidence >= prev.confidence
                        ):
                            merged_by_key[key] = nr
                        elif nr.crop_photo_path and not prev.crop_photo_path:
                            prev.crop_photo_path = nr.crop_photo_path
                    final_results = sorted(merged_by_key.values(), key=lambda r: (r.grid_row, r.grid_col))
                else:
                    final_results = sorted(new_results, key=lambda r: (r.grid_row, r.grid_col))

                zones = cluster_closeup_zones(final_results, grid_rows, grid_cols)
                summary = compute_summary(final_results)

                now = datetime.now(timezone.utc)
                rec.results_json = json.dumps([r.model_dump() for r in final_results])
                if req.zone_id:
                    next_zone = next((z for z in zones if z.zone_id != req.zone_id), zones[0] if zones else None)
                    rec.active_zone_json = next_zone.model_dump_json() if next_zone else None
                elif not zones:
                    rec.active_zone_json = None
                rec.status = "ready_for_review"
                rec.expires_at = _db_ts(thread_db, now + timedelta(seconds=self.TTL_SECONDS))
                thread_db.flush()
                return final_results, zones, summary

        final_results, zones, summary = await anyio.to_thread.run_sync(_persist_scan_results)

        engine_used = "+".join(engine_parts) if engine_parts else "quality_gate"
        msg = (
            f"Scanned {summary.total} compartments: "
            f"{summary.green} green, {summary.yellow} review, {summary.red} need close-up, {summary.unlabeled} empty."
        )
        return LabelScanAnalyzeResponse(
            status="success",
            engine_used=engine_used,
            results=final_results,
            closeup_zones=zones,
            summary=summary,
            message=msg,
        )

    def apply_labels(
        self,
        db: Session,
        token: str,
        req: LabelScanApplyRequest,
    ) -> LabelScanApplyResponse:
        with _GRID_SLOT_LOCK:
            return self._apply_labels_locked(db=db, token=token, req=req)

    def _apply_labels_locked(
        self,
        *,
        db: Session,
        token: str,
        req: LabelScanApplyRequest,
    ) -> LabelScanApplyResponse:
        rec = self._require_active_record(db, token)
        container = location_repository.get_by_id(db, rec.container_id)
        if not container:
            raise NotFoundError("Container not found.")

        if req.save_grid_rows and req.save_grid_cols:
            if not container.grid_rows or not container.grid_cols:
                container.grid_rows = req.save_grid_rows
                container.grid_cols = req.save_grid_cols
                db.flush()

        children_by_id: Dict[int, Location] = {ch.id: ch for ch in (container.children or [])}
        updated_count = 0
        created_slots_count = 0
        created_items_count = 0
        skipped_existing_count = 0
        now = datetime.now(timezone.utc)

        for upd in req.updates:
            target: Optional[Location] = None
            if upd.slot_id is not None:
                target = children_by_id.get(upd.slot_id)
                if target is None:
                    raise BadRequestError(
                        f"Slot {upd.slot_id} does not belong to container '{container.name}'."
                    )
            elif upd.grid_row is not None and upd.grid_col is not None:
                if not container.grid_rows or not container.grid_cols:
                    raise BadRequestError("Container does not have a configured grid layout.")
                slot, created = location_service._ensure_grid_slot_locked(
                    db,
                    container.id,
                    GridSlotEnsureRequest(
                        grid_row=upd.grid_row,
                        grid_col=upd.grid_col,
                        grid_row_span=upd.grid_row_span,
                        grid_col_span=upd.grid_col_span,
                        name=upd.name,
                    ),
                )
                target = slot
                children_by_id[slot.id] = slot
                if created:
                    created_slots_count += 1
                    db.refresh(container)
            else:
                raise BadRequestError("Each update must specify either slot_id or grid_row/grid_col.")

            if is_custom_slot_name(target.name) and not req.overwrite_existing:
                skipped_existing_count += 1
                continue

            target.name = upd.name.strip()[:120]
            if upd.description is not None:
                clean_desc = upd.description.strip()[:2000]
                if clean_desc:
                    target.description = clean_desc
            updated_count += 1

            if upd.create_starter_item:
                starter_item = Item(
                    name=target.name[:160],
                    description=target.description,
                    quantity=1,
                    unit="pcs",
                    location_id=target.id,
                    status="stored",
                    last_seen_at=now,
                )
                db.add(starter_item)
                db.flush()
                db.add(
                    AuditLog(
                        item_id=starter_item.id,
                        location_id=target.id,
                        action="created",
                        notes=f"Auto-labeled in {container.name}",
                        recorded_at=now,
                    )
                )
                created_items_count += 1

        rec.status = "completed"
        rec.active_zone_json = None
        db.flush()
        return LabelScanApplyResponse(
            status="ok",
            updated_count=updated_count,
            created_slots_count=created_slots_count,
            created_items_count=created_items_count,
            skipped_existing_count=skipped_existing_count,
        )


label_scan_service = LabelScanService()
