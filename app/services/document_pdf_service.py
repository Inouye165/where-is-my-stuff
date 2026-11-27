from dataclasses import dataclass
import io
from pathlib import Path
import threading
from typing import Optional
import cv2
import numpy as np
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Image as PlatypusImage, Paragraph, SimpleDocTemplate, Spacer
from app.core.logging_config import logger
from app.services.document_enhance_service import bgr_to_pil
from app.services.document_ocr_service import OcrLineGroup, PageOcrResult, document_ocr_service


@dataclass
class VectorLineSegment:
    x1_norm: float
    y1_norm: float
    x2_norm: float
    y2_norm: float
    thickness_pt: float
    color_rgb: tuple[int, int, int]


@dataclass
class VectorShadedRect:
    x_norm: float
    y_norm: float
    w_norm: float
    h_norm: float
    fill_rgb: tuple[int, int, int]


@dataclass
class ExtractedGraphic:
    x_norm: float
    y_norm: float
    w_norm: float
    h_norm: float
    pil_image: Image.Image


@dataclass
class _FontBundle:
    regular: str
    bold: str
    mono: str
    unicode_safe: bool


_FONT_LOCK = threading.Lock()
_CACHED_FONTS: Optional[_FontBundle] = None


def _ensure_fonts_registered() -> _FontBundle:
    global _CACHED_FONTS
    if _CACHED_FONTS is not None:
        return _CACHED_FONTS
    with _FONT_LOCK:
        if _CACHED_FONTS is not None:
            return _CACHED_FONTS

        candidates = [
            (
                Path(r"C:\Windows\Fonts\arial.ttf"),
                Path(r"C:\Windows\Fonts\arialbd.ttf"),
                Path(r"C:\Windows\Fonts\consola.ttf"),
            ),
            (
                Path(r"C:\Windows\Fonts\segoeui.ttf"),
                Path(r"C:\Windows\Fonts\segoeuib.ttf"),
                Path(r"C:\Windows\Fonts\consola.ttf"),
            ),
            (
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
            ),
            (
                Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
                Path("/System/Library/Fonts/Supplemental/Courier New.ttf"),
            ),
        ]
        for reg_path, bold_path, mono_path in candidates:
            if reg_path.exists():
                try:
                    pdfmetrics.registerFont(TTFont("WimsSans", str(reg_path)))
                    bold_name = "WimsSans"
                    if bold_path.exists():
                        pdfmetrics.registerFont(TTFont("WimsSans-Bold", str(bold_path)))
                        bold_name = "WimsSans-Bold"
                    mono_name = "WimsSans"
                    if mono_path.exists():
                        pdfmetrics.registerFont(TTFont("WimsMono", str(mono_path)))
                        mono_name = "WimsMono"
                    _CACHED_FONTS = _FontBundle(
                        regular="WimsSans",
                        bold=bold_name,
                        mono=mono_name,
                        unicode_safe=True,
                    )
                    return _CACHED_FONTS
                except Exception as exc:
                    logger.warning("Failed to register TTF font %s: %s", reg_path, exc)

        _CACHED_FONTS = _FontBundle(
            regular="Helvetica",
            bold="Helvetica-Bold",
            mono="Courier",
            unicode_safe=False,
        )
        return _CACHED_FONTS


def _safe_text(text: str, unicode_safe: bool) -> str:
    if unicode_safe:
        return text
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _compute_page_size_pt(img_w: int, img_h: int) -> tuple[float, float]:
    aspect = max(0.25, min(4.0, img_w / max(1.0, float(img_h))))
    if aspect <= 1.05:
        page_w = 612.0
        page_h = round(page_w / aspect, 2)
    else:
        page_h = 612.0
        page_w = round(page_h * aspect, 2)
    return page_w, page_h


def _build_text_exclusion_mask(w: int, h: int, ocr: PageOcrResult, pad: int = 4) -> np.ndarray:
    mask = np.zeros((h, w), dtype=np.uint8)
    for sp in ocr.spans:
        x1 = max(0, int(np.floor(sp.x1 - pad)))
        y1 = max(0, int(np.floor(sp.y1 - pad)))
        x2 = min(w, int(np.ceil(sp.x2 + pad)))
        y2 = min(h, int(np.ceil(sp.y2 + pad)))
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    return mask


def extract_vector_structures(
    img_bgr: np.ndarray,
    ocr: PageOcrResult,
) -> tuple[list[VectorShadedRect], list[VectorLineSegment]]:
    """Detect tinted header/callout boxes and horizontal/vertical table/divider lines."""
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    text_mask = _build_text_exclusion_mask(w, h, ocr, pad=3)

    # 1. Detect subtle shaded header/callout boxes (uniform low-variance tinted regions)
    shaded_rects: list[VectorShadedRect] = []
    # Paper is near-white (>242); tinted boxes sit around 175..238
    tint_mask = np.where((gray >= 165) & (gray <= 238) & (text_mask == 0), 255, 0).astype(np.uint8)
    tint_mask = cv2.morphologyEx(tint_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 9)))
    tint_mask = cv2.morphologyEx(tint_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 7)))
    contours, _ = cv2.findContours(tint_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_box_area = w * h * 0.008
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < min_box_area or area > (w * h * 0.65):
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw < 40 or bh < 18:
            continue
        fill_ratio = area / float(bw * bh)
        if fill_ratio < 0.7:
            continue
        patch = img_bgr[by : by + bh, bx : bx + bw]
        sub_mask = text_mask[by : by + bh, bx : bx + bw] == 0
        if int(np.count_nonzero(sub_mask)) < 25:
            continue
        med_bgr = np.median(patch[sub_mask], axis=0)
        shaded_rects.append(
            VectorShadedRect(
                x_norm=bx / w,
                y_norm=by / h,
                w_norm=bw / w,
                h_norm=bh / h,
                fill_rgb=(int(med_bgr[2]), int(med_bgr[1]), int(med_bgr[0])),
            )
        )

    # 2. Detect horizontal & vertical rules outside text boxes
    inv_ink = np.where((gray < 210) & (text_mask == 0), 255, 0).astype(np.uint8)
    lines: list[VectorLineSegment] = []

    h_len = max(24, int(round(w * 0.055)))
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
    h_mask = cv2.morphologyEx(inv_ink, cv2.MORPH_OPEN, h_kernel)
    h_contours, _ = cv2.findContours(h_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in h_contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw >= h_len and bh <= max(8, int(h * 0.012)):
            ym = by + bh * 0.5
            patch = img_bgr[by : by + bh, bx : bx + bw]
            med = np.median(patch.reshape(-1, 3), axis=0)
            lines.append(
                VectorLineSegment(
                    x1_norm=bx / w,
                    y1_norm=ym / h,
                    x2_norm=(bx + bw) / w,
                    y2_norm=ym / h,
                    thickness_pt=float(np.clip(bh * 0.6, 0.6, 2.4)),
                    color_rgb=(int(med[2]), int(med[1]), int(med[0])),
                )
            )

    v_len = max(24, int(round(h * 0.045)))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
    v_mask = cv2.morphologyEx(inv_ink, cv2.MORPH_OPEN, v_kernel)
    v_contours, _ = cv2.findContours(v_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in v_contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bh >= v_len and bw <= max(8, int(w * 0.012)):
            xm = bx + bw * 0.5
            patch = img_bgr[by : by + bh, bx : bx + bw]
            med = np.median(patch.reshape(-1, 3), axis=0)
            lines.append(
                VectorLineSegment(
                    x1_norm=xm / w,
                    y1_norm=by / h,
                    x2_norm=xm / w,
                    y2_norm=(by + bh) / h,
                    thickness_pt=float(np.clip(bw * 0.6, 0.6, 2.4)),
                    color_rgb=(int(med[2]), int(med[1]), int(med[0])),
                )
            )

    return shaded_rects, lines


def extract_non_text_figures(
    img_bgr: np.ndarray,
    ocr: PageOcrResult,
    vector_lines: list[VectorLineSegment],
) -> list[ExtractedGraphic]:
    """Isolate diagrams, charts, schematics, and logos after removing OCR text and vector rules."""
    h, w = img_bgr.shape[:2]
    text_mask = _build_text_exclusion_mask(w, h, ocr, pad=4)

    # Exclude detected vector lines so table borders aren't cropped as giant bitmaps
    line_mask = np.zeros((h, w), dtype=np.uint8)
    for ln in vector_lines:
        cv2.line(
            line_mask,
            (int(round(ln.x1_norm * w)), int(round(ln.y1_norm * h))),
            (int(round(ln.x2_norm * w)), int(round(ln.y2_norm * h))),
            255,
            thickness=6,
        )

    # Foreground mask based on distance from pure white paper (captures colored fills & diagram strokes)
    dist_white = np.max(255 - img_bgr, axis=2)
    fg = np.where((dist_white > 22) & (text_mask == 0) & (line_mask == 0), 255, 0).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    grouped = cv2.dilate(fg, cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)), iterations=1)

    num_labels, _labels, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    min_area = max(900, int(w * h * 0.0025))
    max_area = int(w * h * 0.85)
    figures: list[ExtractedGraphic] = []

    for lbl in range(1, num_labels):
        bx = int(stats[lbl, cv2.CC_STAT_LEFT])
        by = int(stats[lbl, cv2.CC_STAT_TOP])
        bw = int(stats[lbl, cv2.CC_STAT_WIDTH])
        bh = int(stats[lbl, cv2.CC_STAT_HEIGHT])
        box_area = bw * bh
        if bw < 28 or bh < 28 or box_area < min_area or box_area > max_area:
            continue

        # Ensure meaningful non-text foreground actually resides in this region
        fg_sub = fg[by : by + bh, bx : bx + bw]
        if int(np.count_nonzero(fg_sub)) < max(80, int(box_area * 0.04)):
            continue

        crop_bgr = img_bgr[by : by + bh, bx : bx + bw].copy()
        # Cleanly mask OCR text regions inside figure box to pure white so raster/vector text never doubles
        sub_text = text_mask[by : by + bh, bx : bx + bw] > 0
        crop_bgr[sub_text] = (255, 255, 255)
        # Whiten near-white paper pixels for seamless integration on crisp vector page
        near_white = np.min(crop_bgr, axis=2) > 240
        crop_bgr[near_white] = (255, 255, 255)

        pil_fig = bgr_to_pil(crop_bgr)
        figures.append(
            ExtractedGraphic(
                x_norm=bx / w,
                y_norm=by / h,
                w_norm=bw / w,
                h_norm=bh / h,
                pil_image=pil_fig,
            )
        )

    figures.sort(key=lambda f: (f.y_norm, f.x_norm))
    return figures


def _pil_to_jpeg_reader(pil_img: Image.Image, quality: int = 90) -> ImageReader:
    buf = io.BytesIO()
    rgb = pil_img.convert("RGB") if pil_img.mode != "RGB" else pil_img
    rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return ImageReader(buf)


def _draw_layout_matched_page(
    c: canvas.Canvas,
    img_bgr: np.ndarray,
    ocr: PageOcrResult,
    fonts: _FontBundle,
    include_graphics: bool,
) -> None:
    page_w, page_h = _compute_page_size_pt(ocr.width, ocr.height)
    c.setPageSize((page_w, page_h))
    c.setFillColorRGB(1.0, 1.0, 1.0)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)

    if not ocr.spans:
        # Pure diagram / non-text page fallback: embed clean full-page enhanced visual
        pil_full = bgr_to_pil(img_bgr)
        c.drawImage(_pil_to_jpeg_reader(pil_full, quality=92), 0, 0, width=page_w, height=page_h)
        c.showPage()
        return

    shaded_rects, vector_lines = extract_vector_structures(img_bgr, ocr)

    # Layer 1: Vector shaded header / callout fills
    for rect in shaded_rects:
        r, g, b = rect.fill_rgb
        c.setFillColorRGB(r / 255.0, g / 255.0, b / 255.0)
        rx = rect.x_norm * page_w
        ry = page_h - ((rect.y_norm + rect.h_norm) * page_h)
        c.rect(rx, ry, rect.w_norm * page_w, rect.h_norm * page_h, stroke=0, fill=1)

    # Layer 2: Preserved non-text diagrams / charts / logos
    if include_graphics:
        figures = extract_non_text_figures(img_bgr, ocr, vector_lines)
        for fig in figures:
            fx = fig.x_norm * page_w
            fy = page_h - ((fig.y_norm + fig.h_norm) * page_h)
            c.drawImage(
                _pil_to_jpeg_reader(fig.pil_image, quality=92),
                fx,
                fy,
                width=fig.w_norm * page_w,
                height=fig.h_norm * page_h,
                preserveAspectRatio=False,
            )

    # Layer 3: Crisp vector lines & borders
    for ln in vector_lines:
        r, g, b = ln.color_rgb
        c.setStrokeColorRGB(r / 255.0, g / 255.0, b / 255.0)
        c.setLineWidth(ln.thickness_pt)
        c.line(
            ln.x1_norm * page_w,
            page_h - ln.y1_norm * page_h,
            ln.x2_norm * page_w,
            page_h - ln.y2_norm * page_h,
        )

    # Layer 4: Straightened, baseline-aligned vector TrueType text
    scale_x = page_w / max(1.0, float(ocr.width))
    scale_y = page_h / max(1.0, float(ocr.height))

    for line_grp in ocr.lines:
        if not line_grp.spans:
            continue
        # Use a single straight baseline across the line group
        line_bottom_px = sum(sp.y2 for sp in line_grp.spans) / len(line_grp.spans)
        baseline_y_pt = page_h - (line_bottom_px * scale_y) + max(1.0, line_grp.avg_height * scale_y * 0.16)

        for sp in line_grp.spans:
            font_name = fonts.bold if sp.is_bold else fonts.regular
            raw_pt = sp.ink_height * scale_y * 1.12
            font_size = float(np.clip(raw_pt, 6.5, 42.0))
            txt = _safe_text(sp.text, fonts.unicode_safe)
            target_w_pt = max(6.0, sp.width * scale_x)
            measured_w = pdfmetrics.stringWidth(txt, font_name, font_size)

            horiz_scale = 100.0
            if measured_w > 0.5:
                ratio = (target_w_pt / measured_w) * 100.0
                if ratio < 76.0:
                    font_size = max(5.5, font_size * (ratio / 82.0))
                    measured_w = pdfmetrics.stringWidth(txt, font_name, font_size)
                    ratio = (target_w_pt / max(0.5, measured_w)) * 100.0
                horiz_scale = float(np.clip(ratio, 76.0, 108.0))

            r, g, b = sp.color_rgb
            c.setFillColorRGB(r / 255.0, g / 255.0, b / 255.0)
            tx_obj = c.beginText()
            tx_obj.setTextOrigin(sp.x1 * scale_x, baseline_y_pt)
            tx_obj.setFont(font_name, font_size)
            tx_obj.setHorizScale(horiz_scale)
            tx_obj.textOut(txt)
            c.drawText(tx_obj)

    c.showPage()


def _infer_paragraph_blocks(lines: list[OcrLineGroup], page_w: int) -> list[tuple[str, str, float]]:
    """Group straightened OCR lines into (kind, text, font_size_px) blocks for clean_reflow mode."""
    if not lines:
        return []
    heights = [ln.avg_height for ln in lines]
    med_h = float(np.median(heights)) if heights else 14.0
    blocks: list[tuple[str, str, float]] = []

    current_lines: list[str] = []
    current_kind = "body"
    current_h = med_h
    prev_bottom = lines[0].y_bottom

    def _flush() -> None:
        if current_lines:
            blocks.append((current_kind, " ".join(current_lines), current_h))
            current_lines.clear()

    for idx, ln in enumerate(lines):
        line_text = " ".join(sp.text for sp in ln.spans).strip()
        if not line_text:
            continue
        is_heading = ln.avg_height >= med_h * 1.28 or (
            len(ln.spans) == 1 and ln.spans[0].is_bold and len(line_text) <= 80
        )
        kind = "h1" if ln.avg_height >= med_h * 1.48 else ("h2" if is_heading else "body")
        gap = ln.y_top - prev_bottom if idx > 0 else 0.0
        large_gap = gap > max(12.0, med_h * 1.15)
        indented = ln.x_left > page_w * 0.12

        if current_lines and (kind != current_kind or kind != "body" or large_gap or indented):
            _flush()
        current_kind = kind
        current_h = ln.avg_height
        current_lines.append(line_text)
        prev_bottom = ln.y_bottom

    _flush()
    return blocks


def _build_clean_reflow_pdf(
    page_data: list[tuple[np.ndarray, PageOcrResult]],
    fonts: _FontBundle,
    include_graphics: bool,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=48,
        rightMargin=48,
        topMargin=48,
        bottomMargin=48,
        title="Typeset Document (Clean Reflow)",
    )
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "WimsBody",
        parent=styles["Normal"],
        fontName=fonts.regular,
        fontSize=10.5,
        leading=14.5,
        spaceAfter=8,
    )
    h1_style = ParagraphStyle(
        "WimsH1",
        parent=styles["Heading1"],
        fontName=fonts.bold,
        fontSize=16.5,
        leading=21.0,
        spaceBefore=6,
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        "WimsH2",
        parent=styles["Heading2"],
        fontName=fonts.bold,
        fontSize=12.5,
        leading=16.5,
        spaceBefore=6,
        spaceAfter=6,
    )

    story: list = []
    max_content_w = letter[0] - 96

    for img_bgr, ocr in page_data:
        blocks = _infer_paragraph_blocks(ocr.lines, ocr.width)
        for kind, text, _h in blocks:
            escaped = (
                _safe_text(text, fonts.unicode_safe)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            style = h1_style if kind == "h1" else (h2_style if kind == "h2" else body_style)
            story.append(Paragraph(escaped, style))

        if include_graphics:
            _, v_lines = extract_vector_structures(img_bgr, ocr)
            figures = extract_non_text_figures(img_bgr, ocr, v_lines)
            for fig in figures:
                fig_buf = io.BytesIO()
                fig.pil_image.convert("RGB").save(fig_buf, format="JPEG", quality=92)
                fig_buf.seek(0)
                aspect = fig.pil_image.width / max(1, fig.pil_image.height)
                draw_w = min(max_content_w, max(120.0, fig.w_norm * max_content_w))
                draw_h = min(420.0, draw_w / max(0.2, aspect))
                draw_w = draw_h * aspect
                story.append(Spacer(1, 6))
                story.append(PlatypusImage(fig_buf, width=draw_w, height=draw_h))
                story.append(Spacer(1, 8))

        if not blocks and not include_graphics:
            story.append(Paragraph("(No readable text detected on this page.)", body_style))

    doc.build(story)
    return buf.getvalue()


class DocumentPdfService:
    """Creates both high-res Enhanced Scan PDFs and crisp vector Typeset PDFs."""

    def build_enhanced_scan_pdf(self, image_paths: list[Path], pdf_dpi: int = 200) -> bytes:
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.setTitle("Enhanced Document Scan")
        max_px = int(round((pdf_dpi / 72.0) * 860.0))

        for path in image_paths:
            with Image.open(path) as im:
                rgb = im.convert("RGB")
                w, h = rgb.size
                page_w, page_h = _compute_page_size_pt(w, h)
                if max(w, h) > max_px:
                    rgb.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
                c.setPageSize((page_w, page_h))
                c.drawImage(_pil_to_jpeg_reader(rgb, quality=90), 0, 0, width=page_w, height=page_h)
                c.showPage()

        c.save()
        return buf.getvalue()

    def build_typeset_pdf(
        self,
        image_paths: list[Path],
        *,
        mode: str = "layout_matched",
        include_graphics: bool = True,
    ) -> tuple[bytes, int, bool, list[str]]:
        fonts = _ensure_fonts_registered()
        warnings: list[str] = []
        total_words = 0
        any_ocr_available = False
        page_data: list[tuple[np.ndarray, PageOcrResult]] = []

        for idx, path in enumerate(image_paths, start=1):
            with Image.open(path) as im:
                rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
            img_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            ocr = document_ocr_service.extract_page_layout(img_bgr)
            any_ocr_available = any_ocr_available or ocr.ocr_available
            total_words += ocr.word_count
            if ocr.ocr_available and ocr.word_count == 0:
                warnings.append(f"Page {idx}: no printed text detected; preserved page visual.")
            page_data.append((img_bgr, ocr))

        if not any_ocr_available:
            warnings.append("Local OCR engine was unavailable; generated visual-fallback PDF.")

        if mode == "clean_reflow" and total_words > 0:
            pdf_bytes = _build_clean_reflow_pdf(page_data, fonts, include_graphics=include_graphics)
        else:
            buf = io.BytesIO()
            c = canvas.Canvas(buf)
            c.setTitle("Typeset Vector Document")
            for img_bgr, ocr in page_data:
                _draw_layout_matched_page(c, img_bgr, ocr, fonts, include_graphics=include_graphics)
            c.save()
            pdf_bytes = buf.getvalue()

        return pdf_bytes, total_words, any_ocr_available, warnings


document_pdf_service = DocumentPdfService()
