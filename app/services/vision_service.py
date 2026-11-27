import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import anyio
import httpx
from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.core.logging_config import logger
from app.schemas.item import DetectedItem, VisionAnalysisResponse
from app.services.photo_service import photo_service

_GEMINI_MODEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-]{0,127}$")
_GENERIC_MODEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/\-]{0,127}$")


def _validate_model_name(model: str, provider: str) -> str:
    cleaned = model.strip()
    if (
        not cleaned
        or len(cleaned) > 128
        or ".." in cleaned
        or any(ch in cleaned for ch in ("?", "#", "%", "@", "\\"))
        or any(ch.isspace() for ch in cleaned)
    ):
        raise BadRequestError("Invalid vision model identifier.")
    pattern = _GEMINI_MODEL_RE if provider == "gemini" else _GENERIC_MODEL_RE
    if not pattern.match(cleaned):
        raise BadRequestError("Invalid vision model identifier.")
    return cleaned

_VISION_PROMPT = (
    "Analyze this photo of an open storage drawer, bin, tool tray, compartmented assortment container/kit (open or closed lid), or a printed inventory list / packing list / lid chart. "
    "Identify all visible parts, electronic components (such as transistors, resistors, capacitors, potentiometers, ICs, modules), fasteners, screwdrivers, or tools. "
    "CRITICAL: If this is an assortment box, parts kit, printed inventory list, packing list, or lid chart/table listing multiple values, part numbers, or compartments (e.g. '12 Values', '100Ω(101) 5PCS', 'M3*6 Screws 40 Pcs'), you MUST output ONE separate item object in \"items\" for EACH distinct listed item/value/compartment. "
    "If a single chart cell or line lists TWO OR MORE distinct items (for example 'M3*6 Screws 40 Pcs' and 'M3*6+6 mm 10 Pcs' in the same cell), output EACH as its own separate item object with its exact printed count in estimated_qty. NEVER collapse or summarize a multi-value kit or inventory list into fewer items. "
    "Only include printed part numbers, component values, or brand names if clearly readable in the photo; never guess or invent unreadable markings. "
    "Include the specific value or part code in \"name\" or \"part_number\" (e.g. name: 'Square Trimming Potentiometer 100Ω (101)', part_number: '3296-101' or '100Ω(101)', or 'M3*6+6 mm Brass Standoff', '2N2222', '10k ohm'). "
    "In brief_specs, briefly describe what the item is and any visible specs, package type, or markings. "
    "Set estimated_qty to the printed per-cell or per-line count (e.g. 40 for '40 Pcs', 5 for '5PCS') or 1 if no count is shown. "
    "If the items together form a cohesive assortment or kit, set suggested_group_name to a concise descriptive title (e.g. 'M3/M2.5/M2 Brass Spacer Standoff Kit', 'Assorted NPN/PNP Transistors', '3296 Square Trimming Potentiometer Assortment') and suggested_category to its category; otherwise leave suggested_group_name empty. "
    "For each item, ALWAYS include \"bbox\": [x_min, y_min, x_max, y_max] as integers 0-1000 (0=left/top, 1000=right/bottom) framing the physical part/compartment if visible, or that specific value cell/line on the lid chart or inventory list if closed (never omit bbox). "
    "Avoid unescaped double quotes inside string values (write 'in' or 'inch' instead of '\"'). "
    "Return ONLY valid JSON matching: "
    '{"suggested_group_name": "...", "suggested_category": "...", "items": [{"name": "...", "part_number": "...", "category": "...", "estimated_qty": 1, "brief_specs": "...", "bbox": [x_min, y_min, x_max, y_max]}]}'
)


_MULTI_ANGLE_VISION_PROMPT = (
    "You are inspecting MULTIPLE photos taken from different angles or close-ups of the contents inside ONE single storage drawer/bin. "
    "Combine visual details across ALL provided photos (wide overview + side/nameplate/label close-ups) to identify every distinct part, motor, electronic component, fastener, or tool inside this drawer. "
    "CRITICAL MULTI-ANGLE RULES:\n"
    "1. If multiple photos show DIFFERENT SIDES or close-ups of the SAME physical item (for example, photo 1 shows a motor and photo 2 shows its side nameplate/label), merge them into ONE single item entry — DO NOT duplicate the same physical object across angles.\n"
    "2. Identify the specific device/part/motor type whenever any angle shows readable markings, labels, or a nameplate (put exact model/part code in \"part_number\" and clear descriptive title in \"name\").\n"
    "3. Put ALL readable special information from any angle — voltage, current, RPM, gear ratio, specs, pinout, dimensions, package type, or markings — into \"brief_specs\" so it is saved in the item notes.\n"
    "4. Only include part numbers, ratings, or markings that are clearly readable in at least one photo; NEVER guess or invent unreadable digits.\n"
    "5. If the drawer actually contains multiple distinct items or an assortment, output one separate object in \"items\" per distinct item with its count in \"estimated_qty\".\n"
    "6. For each item, include \"bbox\": [x_min, y_min, x_max, y_max] as integers 0-1000 referencing the first photo where it appears.\n"
    "Avoid unescaped double quotes inside string values (write 'in' or 'inch' instead of '\"'). "
    "Return ONLY valid JSON matching: "
    '{"suggested_group_name": "...", "suggested_category": "...", "items": [{"name": "...", "part_number": "...", "category": "...", "estimated_qty": 1, "brief_specs": "...", "bbox": [x_min, y_min, x_max, y_max]}]}'
)


_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _build_vision_prompt(
    expected_count: Optional[int] = None,
    image_count: int = 1,
    context_hint: Optional[str] = None,
) -> str:
    base = _MULTI_ANGLE_VISION_PROMPT if image_count > 1 else _VISION_PROMPT
    parts = [base]
    clean_hint = re.sub(r"\s+", " ", (context_hint or "")).strip()[:240]
    if clean_hint:
        parts.append(
            f"CONTEXT: The drawer label is {clean_hint!r}. Use this only as helpful context; report ONLY items visibly present in the photos."
        )
    if expected_count and expected_count > 0:
        parts.append(
            f"HINT: The user indicates there are approximately {expected_count} distinct item types/values/compartments here. "
            f"Inspect every row, column, and compartment carefully so all {expected_count} distinct items are listed."
        )
    return " ".join(parts)


def _try_parse_json_dict(candidate: str) -> Optional[Dict[str, Any]]:
    for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _recover_items_from_partial_json(text: str) -> List[Dict[str, Any]]:
    """Recover complete item objects from truncated or partially malformed JSON."""
    decoder = json.JSONDecoder()
    recovered: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(text):
        brace_pos = text.find("{", idx)
        if brace_pos == -1:
            break
        # Skip the outer {"items": ...} wrapper by advancing past it if raw_decode fails
        try:
            obj, end_pos = decoder.raw_decode(text, brace_pos)
            if isinstance(obj, dict) and str(obj.get("name", "")).strip():
                recovered.append(obj)
                idx = end_pos
                continue
        except json.JSONDecodeError:
            # Try cleaning trailing comma on just the local object slice up to the next '}'
            close_pos = text.find("}", brace_pos)
            if close_pos != -1:
                snippet = re.sub(r",\s*}$", "}", text[brace_pos : close_pos + 1])
                try:
                    obj = json.loads(snippet)
                    if isinstance(obj, dict) and str(obj.get("name", "")).strip():
                        recovered.append(obj)
                        idx = close_pos + 1
                        continue
                except json.JSONDecodeError:
                    pass
        idx = brace_pos + 1
    return recovered


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    parsed = _try_parse_json_dict(text)
    if parsed is not None:
        return parsed

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        parsed = _try_parse_json_dict(match.group(0))
        if parsed is not None:
            return parsed

    recovered_items = _recover_items_from_partial_json(text)
    if recovered_items:
        logger.info("Recovered %d item(s) from malformed/truncated vision JSON.", len(recovered_items))
        return {"items": recovered_items}

    logger.warning("Vision model returned unparseable JSON (snippet): %s", text[:500])
    raise ValueError("Vision model did not return a valid JSON object.")


def _parse_group_metadata(parsed: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    raw_group = parsed.get("suggested_group_name") or parsed.get("container_summary") or ""
    raw_cat = parsed.get("suggested_category") or ""
    group_name = str(raw_group).strip()[:160] or None
    group_cat = str(raw_cat).strip()[:80] or None
    return group_name, group_cat


def _parse_detected_items(parsed: Dict[str, Any]) -> List[DetectedItem]:
    raw_items = parsed.get("items") or []
    detected: List[DetectedItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            continue
        qty_raw = entry.get("estimated_qty", 1)
        try:
            qty = max(1, int(qty_raw)) if qty_raw is not None else 1
        except (TypeError, ValueError):
            qty = 1
        raw_bbox = (
            entry.get("bbox")
            or entry.get("bbox_2d")
            or entry.get("box_2d")
            or entry.get("bounding_box")
        )
        bbox: Optional[List[float]] = None
        if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
            try:
                bbox = [float(c) for c in raw_bbox]
            except (TypeError, ValueError):
                bbox = None
        raw_part_no = entry.get("part_number") or entry.get("serial_or_part_number")
        part_number = str(raw_part_no).strip()[:120] if raw_part_no and str(raw_part_no).strip() else None
        detected.append(
            DetectedItem(
                name=str(entry["name"]).strip()[:160],
                part_number=part_number,
                category=str(entry["category"]).strip()[:80] if entry.get("category") else None,
                estimated_qty=qty,
                brief_specs=str(entry["brief_specs"]).strip()[:200] if entry.get("brief_specs") else None,
                bbox=bbox,
            )
        )
    return detected


class VisionService:
    """Identifies items in drawer/bin photos using a local (Ollama) or paid (Gemini/OpenAI) model."""

    @staticmethod
    def _effective_gemini_key(override: Optional[str] = None) -> Optional[str]:
        return (override or os.environ.get("GEMINI_API_KEY") or settings.GEMINI_API_KEY or "").strip() or None

    @staticmethod
    def _effective_openai_key(override: Optional[str] = None) -> Optional[str]:
        return (override or os.environ.get("OPENAI_API_KEY") or settings.OPENAI_API_KEY or "").strip() or None

    @staticmethod
    def _effective_ollama_url() -> str:
        return (os.environ.get("OLLAMA_BASE_URL") or settings.OLLAMA_BASE_URL or "http://localhost:11434").rstrip("/")

    @staticmethod
    def _effective_ollama_model(override: Optional[str] = None) -> str:
        return (override or os.environ.get("OLLAMA_VISION_MODEL") or settings.OLLAMA_VISION_MODEL or "qwen2.5vl:7b").strip()

    async def get_provider_status(self) -> Dict[str, Any]:
        ollama_url = self._effective_ollama_url()
        ollama_model = self._effective_ollama_model()
        ollama_available = False
        installed_models: List[str] = []

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                if resp.status_code == 200:
                    ollama_available = True
                    data = resp.json()
                    installed_models = [
                        m.get("name", "") for m in data.get("models", []) if isinstance(m, dict) and m.get("name")
                    ]
        except Exception:
            ollama_available = False

        gemini_available = bool(self._effective_gemini_key())
        openai_available = bool(self._effective_openai_key())

        default_provider = (os.environ.get("VISION_PROVIDER") or settings.VISION_PROVIDER or "local").lower()
        if default_provider == "auto":
            if ollama_available:
                default_provider = "local"
            elif gemini_available:
                default_provider = "gemini"
            elif openai_available:
                default_provider = "openai"
            else:
                default_provider = "local"

        return {
            "default_provider": default_provider,
            "providers": {
                "local": {
                    "label": "Local Model (Ollama)",
                    "available": ollama_available,
                    "base_url": ollama_url,
                    "model": ollama_model,
                    "installed_models": installed_models,
                },
                "gemini": {
                    "label": "Paid: Google Gemini",
                    "available": gemini_available,
                    "model": settings.GEMINI_VISION_MODEL,
                },
                "openai": {
                    "label": "Paid: OpenAI Vision",
                    "available": openai_available,
                    "model": settings.OPENAI_VISION_MODEL,
                },
            },
        }

    @staticmethod
    def _normalize_image_inputs(
        images_or_b64: Any, mime_type: str = "image/jpeg"
    ) -> List[tuple[str, str]]:
        if isinstance(images_or_b64, str):
            return [(images_or_b64, mime_type or "image/jpeg")]
        return [
            (str(b64), str(mt or "image/jpeg"))
            for b64, mt in (images_or_b64 or [])
            if b64
        ]

    async def _analyze_with_ollama(
        self,
        images_or_b64: Any,
        model: str,
        expected_count: Optional[int] = None,
        context_hint: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        ollama_url = self._effective_ollama_url()
        img_list = self._normalize_image_inputs(images_or_b64)
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": _build_vision_prompt(
                        expected_count, image_count=len(img_list), context_hint=context_hint
                    ),
                    "images": [b64 for b64, _ in img_list],
                }
            ],
            "format": "json",
            "stream": False,
            "options": {
                "num_predict": 4096,
                "temperature": 0.1,
            },
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{ollama_url}/api/chat", json=payload)
            if resp.status_code != 200:
                err_detail = ""
                try:
                    err_detail = resp.json().get("error", "")
                except Exception:
                    pass
                detail_suffix = f" ({err_detail})" if err_detail else ""
                return VisionAnalysisResponse(
                    status="fallback",
                    items=[],
                    has_vision_api=False,
                    provider_used="local",
                    model_used=model,
                    message=f"Local model ({model}) returned HTTP {resp.status_code}{detail_suffix}.",
                )
            resp_data = resp.json()
            raw_content = (resp_data.get("message") or {}).get("content") or resp_data.get("response") or ""
            parsed = _extract_json_object(raw_content)
            detected = _parse_detected_items(parsed)
            group_name, group_cat = _parse_group_metadata(parsed)
            return VisionAnalysisResponse(
                status="success",
                items=detected,
                suggested_group_name=group_name,
                suggested_category=group_cat,
                has_vision_api=True,
                provider_used="local",
                model_used=model,
                message=f"Local model ({model}) detected {len(detected)} item(s).",
            )

    @classmethod
    def _build_gemini_payload(
        cls, prompt: str, images_or_b64: Any, mime_type: str = "image/jpeg"
    ) -> dict[str, Any]:
        parts: List[dict[str, Any]] = [{"text": prompt}]
        for b64, mt in cls._normalize_image_inputs(images_or_b64, mime_type):
            parts.append({"inline_data": {"mime_type": mt, "data": b64}})
        return {
            "contents": [{"parts": parts}],
            "generationConfig": {"response_mime_type": "application/json"},
        }

    @classmethod
    def _build_openai_payload(
        cls, prompt: str, model: str, images_or_b64: Any, mime_type: str = "image/jpeg"
    ) -> dict[str, Any]:
        content: List[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for b64, mt in cls._normalize_image_inputs(images_or_b64, mime_type):
            content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}})
        return {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }

    async def _analyze_with_gemini(
        self,
        images_or_b64: Any,
        model: str,
        api_key: str,
        expected_count: Optional[int] = None,
        mime_type: str = "image/jpeg",
        context_hint: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        safe_model = _validate_model_name(model, "gemini")
        img_list = self._normalize_image_inputs(images_or_b64, mime_type)
        prompt = _build_vision_prompt(expected_count, image_count=len(img_list), context_hint=context_hint)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{safe_model}:generateContent"
        payload = self._build_gemini_payload(prompt, img_list)
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload, headers={"x-goog-api-key": api_key})
            if resp.status_code != 200:
                return VisionAnalysisResponse(
                    status="fallback",
                    items=[],
                    has_vision_api=True,
                    provider_used="gemini",
                    model_used=safe_model,
                    message=f"Gemini vision returned HTTP {resp.status_code}. Verify your GEMINI_API_KEY.",
                )
            resp_data = resp.json()
            text_content = resp_data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json_object(text_content)
            detected = _parse_detected_items(parsed)
            group_name, group_cat = _parse_group_metadata(parsed)
            return VisionAnalysisResponse(
                status="success",
                items=detected,
                suggested_group_name=group_name,
                suggested_category=group_cat,
                has_vision_api=True,
                provider_used="gemini",
                model_used=safe_model,
                message=f"Gemini ({safe_model}) detected {len(detected)} item(s).",
            )

    async def _analyze_with_openai(
        self,
        images_or_b64: Any,
        model: str,
        api_key: str,
        expected_count: Optional[int] = None,
        mime_type: str = "image/jpeg",
        context_hint: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        safe_model = _validate_model_name(model, "openai")
        img_list = self._normalize_image_inputs(images_or_b64, mime_type)
        prompt = _build_vision_prompt(expected_count, image_count=len(img_list), context_hint=context_hint)
        payload = self._build_openai_payload(prompt, safe_model, img_list)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            if resp.status_code != 200:
                return VisionAnalysisResponse(
                    status="fallback",
                    items=[],
                    has_vision_api=True,
                    provider_used="openai",
                    model_used=safe_model,
                    message=f"OpenAI vision returned HTTP {resp.status_code}. Verify your OPENAI_API_KEY.",
                )
            resp_data = resp.json()
            text_content = resp_data["choices"][0]["message"]["content"]
            parsed = _extract_json_object(text_content)
            detected = _parse_detected_items(parsed)
            group_name, group_cat = _parse_group_metadata(parsed)
            return VisionAnalysisResponse(
                status="success",
                items=detected,
                suggested_group_name=group_name,
                suggested_category=group_cat,
                has_vision_api=True,
                provider_used="openai",
                model_used=safe_model,
                message=f"OpenAI ({safe_model}) detected {len(detected)} item(s).",
            )

    async def analyze_image(
        self,
        image_path: Optional[Path],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        expected_count: Optional[int] = None,
        context_hint: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        return await self.analyze_images(
            [image_path] if image_path else [],
            provider=provider,
            model=model,
            expected_count=expected_count,
            context_hint=context_hint,
        )

    async def analyze_images(
        self,
        image_paths: List[Path],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        expected_count: Optional[int] = None,
        context_hint: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        valid_paths = [p for p in (image_paths or []) if p is not None]

        def _read_encoded_images() -> List[tuple[Path, str, str]]:
            encoded: List[tuple[Path, str, str]] = []
            for p in valid_paths:
                if not p.exists():
                    continue
                b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
                mime = _MIME_BY_SUFFIX.get(p.suffix.lower(), "image/jpeg")
                encoded.append((p, b64, mime))
            return encoded

        encoded_items = await anyio.to_thread.run_sync(_read_encoded_images)
        if not encoded_items:
            return VisionAnalysisResponse(
                status="fallback",
                items=[],
                has_vision_api=bool(self._effective_gemini_key() or self._effective_openai_key()),
                message="Photo uploaded. Enter items line-by-line below to batch-add them to this drawer.",
            )

        primary_path = encoded_items[0][0]
        encoded_pairs = [(b64, mime) for _, b64, mime in encoded_items]
        selected = (provider or os.environ.get("VISION_PROVIDER") or settings.VISION_PROVIDER or "").strip().lower()
        gemini_key = self._effective_gemini_key()
        openai_key = self._effective_openai_key()

        if not selected or selected == "auto":
            selected = "gemini" if gemini_key else ("openai" if openai_key else "local")

        target_model: Optional[str] = None
        if selected == "gemini":
            target_model = _validate_model_name(model or settings.GEMINI_VISION_MODEL, "gemini")
        elif selected == "openai":
            target_model = _validate_model_name(model or settings.OPENAI_VISION_MODEL, "openai")
        else:
            target_model = _validate_model_name(self._effective_ollama_model(model), "local")

        try:
            if selected == "gemini":
                if not gemini_key:
                    return VisionAnalysisResponse(
                        status="fallback",
                        items=[],
                        has_vision_api=False,
                        provider_used="gemini",
                        message="GEMINI_API_KEY is not configured. Add it to .env or switch to Local Model (Ollama).",
                    )
                result = await self._analyze_with_gemini(
                    encoded_pairs,
                    target_model,
                    gemini_key,
                    expected_count=expected_count,
                    context_hint=context_hint,
                )
            elif selected == "openai":
                if not openai_key:
                    return VisionAnalysisResponse(
                        status="fallback",
                        items=[],
                        has_vision_api=False,
                        provider_used="openai",
                        message="OPENAI_API_KEY is not configured. Add it to .env or switch to Local Model (Ollama).",
                    )
                result = await self._analyze_with_openai(
                    encoded_pairs,
                    target_model,
                    openai_key,
                    expected_count=expected_count,
                    context_hint=context_hint,
                )
            else:
                # Default: local (Ollama)
                result = await self._analyze_with_ollama(
                    encoded_pairs,
                    target_model,
                    expected_count=expected_count,
                    context_hint=context_hint,
                )

            if result.status == "success" and result.items and primary_path.exists():
                def _crop_all() -> int:
                    all_coords = [c for it in result.items if it.bbox for c in it.bbox]
                    batch_mode = "auto"
                    if all_coords:
                        max_all = max(all_coords)
                        if max_all <= 1.0:
                            batch_mode = "norm_1"
                        elif max_all > 1000.0:
                            batch_mode = "pixels"
                        else:
                            batch_mode = "norm_1000"
                    crops = photo_service.crop_many_and_save(
                        primary_path,
                        [it.bbox for it in result.items],
                        coord_mode=batch_mode,
                    )
                    cropped_count = 0
                    for item, crop_path in zip(result.items, crops):
                        item.crop_photo_path = crop_path
                        if crop_path:
                            cropped_count += 1
                    return cropped_count

                cropped_count = await anyio.to_thread.run_sync(_crop_all)
                logger.info(
                    "Vision (%s) analyzed %d image(s), detected %d item(s), cropped %d bounding box(es).",
                    selected,
                    len(encoded_pairs),
                    len(result.items),
                    cropped_count,
                )
            return result
        except BadRequestError:
            raise
        except (httpx.RequestError, httpx.TimeoutException, ConnectionError, OSError) as exc:
            logger.warning("Vision analysis (%s) connection failed: %s", selected, exc)
            return VisionAnalysisResponse(
                status="fallback",
                items=[],
                has_vision_api=selected in ("gemini", "openai"),
                provider_used=selected,
                message=(
                    f"Could not reach {selected} vision model ({exc.__class__.__name__}). "
                    "You can still enter items manually below."
                ),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("Vision analysis (%s) response parse failed: %s", selected, exc)
            return VisionAnalysisResponse(
                status="fallback",
                items=[],
                has_vision_api=selected in ("gemini", "openai"),
                provider_used=selected,
                message=(
                    f"{selected.capitalize()} vision model returned an unreadable response ({exc.__class__.__name__}). "
                    "You can still enter items manually below."
                ),
            )
        except Exception as exc:
            logger.warning("Vision analysis (%s) failed: %s", selected, exc, exc_info=True)
            return VisionAnalysisResponse(
                status="fallback",
                items=[],
                has_vision_api=selected in ("gemini", "openai"),
                provider_used=selected,
                message=(
                    f"{selected.capitalize()} vision model failed ({exc.__class__.__name__}). "
                    "You can still enter items manually below."
                ),
            )

    async def extract_search_query(
        self,
        raw_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> tuple[str, bool]:
        prompt = (
            "Identify the main tool, part, device, or household item in this photo. "
            'Return JSON: {"query": "short 1-3 word search phrase"}'
        )
        b64_data = await anyio.to_thread.run_sync(lambda: base64.b64encode(raw_bytes).decode("utf-8"))
        gemini_key = self._effective_gemini_key()
        openai_key = self._effective_openai_key()
        has_vision_api = bool(gemini_key or openai_key)

        if gemini_key:
            try:
                model = _validate_model_name(settings.GEMINI_VISION_MODEL, "gemini")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                payload = self._build_gemini_payload(prompt, b64_data, mime_type or "image/jpeg")
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=payload, headers={"x-goog-api-key": gemini_key})
                if resp.status_code == 200:
                    text_content = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    query = str(_extract_json_object(text_content).get("query", "")).strip()
                    if query:
                        return query, has_vision_api
                else:
                    logger.warning("Vision search query (gemini) returned HTTP %s", resp.status_code)
            except Exception as exc:
                logger.warning("Vision search query extraction (gemini) failed: %s", exc)

        if openai_key:
            try:
                model = _validate_model_name(settings.OPENAI_VISION_MODEL, "openai")
                payload = self._build_openai_payload(prompt, model, b64_data, mime_type or "image/jpeg")
                headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
                if resp.status_code == 200:
                    text_content = resp.json()["choices"][0]["message"]["content"]
                    query = str(_extract_json_object(text_content).get("query", "")).strip()
                    if query:
                        return query, has_vision_api
                else:
                    logger.warning("Vision search query (openai) returned HTTP %s", resp.status_code)
            except Exception as exc:
                logger.warning("Vision search query extraction (openai) failed: %s", exc)

        return "", has_vision_api


vision_service = VisionService()

