import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from app.core.config import settings
from app.core.logging_config import logger
from app.schemas.item import DetectedItem, VisionAnalysisResponse

_VISION_PROMPT = (
    "Analyze this photo of an open storage drawer, bin, or cabinet. "
    "Identify all visible tools, electronic modules, parts, or hardware components. "
    "Read any printed part numbers or board markings (e.g., MT3608, ESP32, LM2596, TP4056) and use them in the name. "
    "In brief_specs, explain what the item does and any key technical specs (voltage/current/pinout/size) if known. "
    "Return ONLY valid JSON matching: "
    '{"items": [{"name": "...", "category": "...", "estimated_qty": 1, "brief_specs": "..."}]}'
)


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Vision model did not return a valid JSON object.")


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
        detected.append(
            DetectedItem(
                name=str(entry["name"]).strip()[:160],
                category=str(entry["category"]).strip()[:80] if entry.get("category") else None,
                estimated_qty=qty,
                brief_specs=str(entry["brief_specs"]).strip()[:200] if entry.get("brief_specs") else None,
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

    async def _analyze_with_ollama(self, b64_data: str, model: str) -> VisionAnalysisResponse:
        ollama_url = self._effective_ollama_url()
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": _VISION_PROMPT,
                    "images": [b64_data],
                }
            ],
            "format": "json",
            "stream": False,
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
            return VisionAnalysisResponse(
                status="success",
                items=detected,
                has_vision_api=True,
                provider_used="local",
                model_used=model,
                message=f"Local model ({model}) detected {len(detected)} item(s).",
            )

    async def _analyze_with_gemini(self, b64_data: str, model: str, api_key: str) -> VisionAnalysisResponse:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _VISION_PROMPT},
                        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}},
                    ]
                }
            ],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                return VisionAnalysisResponse(
                    status="fallback",
                    items=[],
                    has_vision_api=True,
                    provider_used="gemini",
                    model_used=model,
                    message=f"Gemini vision returned HTTP {resp.status_code}. Verify your GEMINI_API_KEY.",
                )
            resp_data = resp.json()
            text_content = resp_data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json_object(text_content)
            detected = _parse_detected_items(parsed)
            return VisionAnalysisResponse(
                status="success",
                items=detected,
                has_vision_api=True,
                provider_used="gemini",
                model_used=model,
                message=f"Gemini ({model}) detected {len(detected)} item(s).",
            )

    async def _analyze_with_openai(self, b64_data: str, model: str, api_key: str) -> VisionAnalysisResponse:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"}},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            if resp.status_code != 200:
                return VisionAnalysisResponse(
                    status="fallback",
                    items=[],
                    has_vision_api=True,
                    provider_used="openai",
                    model_used=model,
                    message=f"OpenAI vision returned HTTP {resp.status_code}. Verify your OPENAI_API_KEY.",
                )
            resp_data = resp.json()
            text_content = resp_data["choices"][0]["message"]["content"]
            parsed = _extract_json_object(text_content)
            detected = _parse_detected_items(parsed)
            return VisionAnalysisResponse(
                status="success",
                items=detected,
                has_vision_api=True,
                provider_used="openai",
                model_used=model,
                message=f"OpenAI ({model}) detected {len(detected)} item(s).",
            )

    async def analyze_image(
        self,
        image_path: Optional[Path],
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> VisionAnalysisResponse:
        if not image_path or not image_path.exists():
            return VisionAnalysisResponse(
                status="fallback",
                items=[],
                has_vision_api=bool(self._effective_gemini_key() or self._effective_openai_key()),
                message="Photo uploaded. Enter items line-by-line below to batch-add them to this drawer.",
            )

        selected = (provider or os.environ.get("VISION_PROVIDER") or settings.VISION_PROVIDER or "").strip().lower()
        gemini_key = self._effective_gemini_key()
        openai_key = self._effective_openai_key()

        if not selected or selected == "auto":
            selected = "gemini" if gemini_key else ("openai" if openai_key else "local")

        b64_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

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
                return await self._analyze_with_gemini(b64_data, model or settings.GEMINI_VISION_MODEL, gemini_key)

            if selected == "openai":
                if not openai_key:
                    return VisionAnalysisResponse(
                        status="fallback",
                        items=[],
                        has_vision_api=False,
                        provider_used="openai",
                        message="OPENAI_API_KEY is not configured. Add it to .env or switch to Local Model (Ollama).",
                    )
                return await self._analyze_with_openai(b64_data, model or settings.OPENAI_VISION_MODEL, openai_key)

            # Default: local (Ollama)
            ollama_model = self._effective_ollama_model(model)
            return await self._analyze_with_ollama(b64_data, ollama_model)
        except Exception as exc:
            logger.warning("Vision analysis (%s) failed: %s", selected, exc)
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


vision_service = VisionService()

