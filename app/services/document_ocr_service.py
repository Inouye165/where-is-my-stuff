from dataclasses import dataclass, field
import statistics
import threading
from typing import Any
import cv2
import numpy as np
from app.core.logging_config import logger


@dataclass
class OcrTextSpan:
    text: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    polygon: list[tuple[float, float]]
    ink_height: float
    is_bold: bool
    color_rgb: tuple[int, int, int]

    @property
    def height(self) -> float:
        return max(1.0, self.y2 - self.y1)

    @property
    def width(self) -> float:
        return max(1.0, self.x2 - self.x1)

    @property
    def y_center(self) -> float:
        return (self.y1 + self.y2) * 0.5


@dataclass
class OcrLineGroup:
    spans: list[OcrTextSpan] = field(default_factory=list)
    y_top: float = 0.0
    y_bottom: float = 0.0
    x_left: float = 0.0
    x_right: float = 0.0
    avg_height: float = 12.0


@dataclass
class PageOcrResult:
    width: int
    height: int
    spans: list[OcrTextSpan]
    lines: list[OcrLineGroup]
    word_count: int
    ocr_available: bool


class DocumentOcrService:
    """Thread-safe RapidOCR layout and style extractor for document pages."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: Any = None
        self._init_failed = False

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        if self._init_failed:
            return None
        with self._lock:
            if self._engine is not None:
                return self._engine
            if self._init_failed:
                return None
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
                return self._engine
            except Exception as exc:
                self._init_failed = True
                logger.warning("RapidOCR unavailable for document typesetting: %s", exc)
                return None

    @staticmethod
    def _sample_span_style(
        img_bgr: np.ndarray,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> tuple[float, float, tuple[int, int, int]]:
        h, w = img_bgr.shape[:2]
        ix1 = max(0, min(w - 1, int(math_floor(x1))))
        iy1 = max(0, min(h - 1, int(math_floor(y1))))
        ix2 = max(ix1 + 1, min(w, int(math_ceil(x2))))
        iy2 = max(iy1 + 1, min(h, int(math_ceil(y2))))
        crop = img_bgr[iy1:iy2, ix1:ix2]
        if crop.size == 0:
            return max(4.0, y2 - y1), 0.15, (18, 18, 18)

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        bg_luma = float(np.percentile(gray, 90))
        ink_thresh = min(195.0, max(70.0, bg_luma - 38.0))
        ink_mask = gray <= ink_thresh
        ink_count = int(np.count_nonzero(ink_mask))
        box_h = max(4.0, float(iy2 - iy1))
        if ink_count < 3:
            return box_h, 0.15, (18, 18, 18)

        ys, _xs = np.where(ink_mask)
        tight_h = float(int(ys.max()) - int(ys.min()) + 1)
        ink_height = max(box_h * 0.55, tight_h)
        density = ink_count / float(crop.shape[0] * crop.shape[1])

        ink_bgr = crop[ink_mask]
        med_b, med_g, med_r = [int(round(v)) for v in np.median(ink_bgr, axis=0)]
        chroma = max(med_r, med_g, med_b) - min(med_r, med_g, med_b)
        if max(med_r, med_g, med_b) < 78 and chroma < 28:
            color_rgb = (18, 18, 18)
        else:
            color_rgb = (
                max(0, min(220, med_r)),
                max(0, min(220, med_g)),
                max(0, min(220, med_b)),
            )
        return ink_height, density, color_rgb

    def detect_upright_rotation(self, img_bgr: np.ndarray) -> int:
        """Return clockwise degrees (0, 90, 180, 270) needed to orient printed text upright."""
        engine = self._get_engine()
        if engine is None or img_bgr is None or img_bgr.size == 0:
            return 0

        h, w = img_bgr.shape[:2]
        scale = min(1.0, 900.0 / max(1, w, h))
        work = (
            cv2.resize(img_bgr, (max(64, int(round(w * scale))), max(64, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
            if scale < 0.98
            else img_bgr
        )

        def _score_horizontal_and_vertical(im: np.ndarray) -> tuple[float, float]:
            contig = np.ascontiguousarray(im, dtype=np.uint8)
            with self._lock:
                res, _ = engine(contig, use_cls=False)
            h_score = 0.0
            v_score = 0.0
            for entry in (res or []):
                if not entry or len(entry) < 3:
                    continue
                box, txt, conf = entry[0], str(entry[1] or "").strip(), float(entry[2] or 0.0)
                if not txt or conf < 0.45 or not box:
                    continue
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                bw = max(xs) - min(xs)
                bh = max(ys) - min(ys)
                weight = conf * len(txt)
                if bw > bh * 1.15:
                    h_score += weight
                elif bh > bw * 1.15:
                    v_score += weight
            return h_score, v_score

        # Fast path: check 0 deg first; if text is already clearly horizontal & readable, stop immediately
        h0, v0 = _score_horizontal_and_vertical(work)
        if h0 >= 12.0 and h0 >= v0 * 1.8:
            return 0

        scores: dict[int, float] = {0: h0}
        candidates = (
            [(90, cv2.ROTATE_90_CLOCKWISE), (270, cv2.ROTATE_90_COUNTERCLOCKWISE), (180, cv2.ROTATE_180)]
            if v0 >= h0
            else [(180, cv2.ROTATE_180), (90, cv2.ROTATE_90_CLOCKWISE), (270, cv2.ROTATE_90_COUNTERCLOCKWISE)]
        )
        for deg, rot_code in candidates:
            rot_im = cv2.rotate(work, rot_code)
            hs, _vs = _score_horizontal_and_vertical(rot_im)
            scores[deg] = hs
            if hs >= 18.0 and hs >= max(scores.get(0, 0.0), 1.0) * 1.8:
                return deg

        best_deg = max(scores, key=lambda d: scores[d])
        if scores[best_deg] < 4.0 or (best_deg != 0 and scores[best_deg] <= scores[0] * 1.25):
            return 0
        return best_deg

    def extract_page_layout(self, img_bgr: np.ndarray) -> PageOcrResult:
        h, w = img_bgr.shape[:2]
        engine = self._get_engine()
        if engine is None:
            return PageOcrResult(width=w, height=h, spans=[], lines=[], word_count=0, ocr_available=False)

        # Upscale smaller pages slightly so fine text reads cleanly
        scale = 1.0
        if max(w, h) < 1600:
            scale = min(2.0, 1800.0 / max(1, w, h))
        ocr_input = (
            cv2.resize(img_bgr, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_CUBIC)
            if scale > 1.05
            else img_bgr
        )

        ocr_input = np.ascontiguousarray(ocr_input, dtype=np.uint8)
        with self._lock:
            raw_res, _ = engine(ocr_input)

        if not raw_res:
            return PageOcrResult(width=w, height=h, spans=[], lines=[], word_count=0, ocr_available=True)

        spans: list[OcrTextSpan] = []
        densities: list[float] = []
        word_count = 0

        for entry in raw_res:
            if not entry or len(entry) < 3:
                continue
            box_pts, text, conf = entry[0], str(entry[1] or "").strip(), float(entry[2] or 0.0)
            if not text or conf < 0.25 or not box_pts:
                continue
            poly = [(float(pt[0]) / scale, float(pt[1]) / scale) for pt in box_pts]
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            x1 = max(0.0, min(xs))
            y1 = max(0.0, min(ys))
            x2 = min(float(w), max(xs))
            y2 = min(float(h), max(ys))
            if (x2 - x1) < 2 or (y2 - y1) < 2:
                continue

            ink_h, density, color_rgb = self._sample_span_style(img_bgr, x1, y1, x2, y2)
            densities.append(density)
            word_count += len(text.split())
            spans.append(
                OcrTextSpan(
                    text=text,
                    confidence=conf,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    polygon=poly,
                    ink_height=ink_h,
                    is_bold=False,
                    color_rgb=color_rgb,
                )
            )

        if spans and densities:
            med_density = statistics.median(densities)
            heights = [sp.ink_height for sp in spans]
            med_height = statistics.median(heights) if heights else 14.0
            for sp, dens in zip(spans, densities, strict=True):
                sp.is_bold = bool(dens >= med_density * 1.24 or sp.ink_height >= med_height * 1.32)

        lines = self._group_into_lines(spans, page_w=w)
        return PageOcrResult(
            width=w,
            height=h,
            spans=spans,
            lines=lines,
            word_count=word_count,
            ocr_available=True,
        )

    @staticmethod
    def _group_into_lines(spans: list[OcrTextSpan], page_w: int) -> list[OcrLineGroup]:
        if not spans:
            return []

        sorted_spans = sorted(spans, key=lambda s: (s.y_center, s.x1))
        groups: list[list[OcrTextSpan]] = []
        for sp in sorted_spans:
            placed = False
            for grp in groups:
                ref_y = statistics.mean(g.y_center for g in grp)
                ref_h = statistics.mean(g.height for g in grp)
                tol = max(4.0, min(sp.height, ref_h) * 0.55)
                if abs(sp.y_center - ref_y) <= tol:
                    grp.append(sp)
                    placed = True
                    break
            if not placed:
                groups.append([sp])

        line_groups: list[OcrLineGroup] = []
        for grp in groups:
            grp.sort(key=lambda s: s.x1)
            y_top = min(s.y1 for s in grp)
            y_bot = max(s.y2 for s in grp)
            avg_h = statistics.mean(s.ink_height for s in grp)
            line_groups.append(
                OcrLineGroup(
                    spans=grp,
                    y_top=y_top,
                    y_bottom=y_bot,
                    x_left=grp[0].x1,
                    x_right=grp[-1].x2,
                    avg_height=avg_h,
                )
            )

        line_groups.sort(key=lambda ln: ln.y_top)

        # Snap left-aligned column edges within 1.4% of page width so straightened columns line up cleanly
        snap_tol = max(6.0, page_w * 0.014)
        anchors: list[list[OcrTextSpan]] = []
        for ln in line_groups:
            if not ln.spans:
                continue
            lead = ln.spans[0]
            matched = False
            for bucket in anchors:
                anchor_x = statistics.median(b.x1 for b in bucket)
                if abs(lead.x1 - anchor_x) <= snap_tol:
                    bucket.append(lead)
                    matched = True
                    break
            if not matched:
                anchors.append([lead])

        for bucket in anchors:
            if len(bucket) >= 2:
                snapped_x = statistics.median(b.x1 for b in bucket)
                for span in bucket:
                    delta = snapped_x - span.x1
                    span.x1 = snapped_x
                    span.x2 = max(span.x1 + 4.0, span.x2 + delta)

        for ln in line_groups:
            if ln.spans:
                ln.x_left = ln.spans[0].x1
                ln.x_right = ln.spans[-1].x2
        return line_groups


def math_floor(val: float) -> int:
    return int(np.floor(val))


def math_ceil(val: float) -> int:
    return int(np.ceil(val))


document_ocr_service = DocumentOcrService()
