import base64
import io
import json
import os
import re
from typing import List, Optional, Set
from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.repositories.item_repository import item_repository
from app.repositories.location_repository import location_repository
from app.schemas.item import ItemRead
from app.services.inventory_service import inventory_service

_STOP_WORDS: Set[str] = {
    "a", "an", "the", "to", "for", "with", "in", "on", "of", "by", "at",
    "something", "anything", "thing", "item", "stuff", "object", "part",
    "that", "which", "used", "my", "i", "need", "find", "looking", "is",
    "it", "are", "do", "does", "can", "you", "me", "some", "get",
}

_PLAIN_SYNONYMS = {
    "cut": ["knife", "blade", "cutter", "scissors", "shear", "saw"],
    "cardboard": ["box", "utility", "cutter", "knife"],
    "tape": ["adhesive", "masking", "duct", "packing"],
    "light": ["led", "lamp", "flashlight", "bulb", "torch"],
    "power": ["battery", "charger", "converter", "supply", "usb", "adapter"],
    "wire": ["cable", "lead", "jumper", "cord", "harness"],
    "screw": ["bolt", "nut", "fastener", "driver"],
    "glue": ["adhesive", "epoxy", "cement", "tape"],
    "hot": ["heat", "rework", "solder", "thermal"],
    "charge": ["charger", "charging", "tp4056", "usb", "battery"],
}


def _extract_meaningful_tokens(query: str) -> List[str]:
    raw = re.findall(r"[a-zA-Z0-9\-_.]+", query.lower())
    filtered = [t for t in raw if t not in _STOP_WORDS and len(t) > 1]
    if not filtered:
        filtered = [t for t in raw if len(t) > 0]
    expanded: List[str] = []
    seen: Set[str] = set()
    for tok in filtered:
        if tok not in seen:
            seen.add(tok)
            expanded.append(tok)
        for syn in _PLAIN_SYNONYMS.get(tok, ()):
            if syn not in seen:
                seen.add(syn)
                expanded.append(syn)
    return expanded


def _keywords_from_filename(filename: Optional[str]) -> str:
    if not filename:
        return ""
    stem = os.path.splitext(os.path.basename(filename))[0]
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", stem).strip()
    if cleaned.lower() in {"image", "photo", "img", "capture", "snap", "picture", "camera", "upload"}:
        return ""
    return cleaned


class SearchService:
    @classmethod
    def search(cls, db: Session, query: str, limit: int = 100) -> List[ItemRead]:
        cleaned = query.strip()
        if not cleaned:
            recent = item_repository.list_recent(db, limit=limit)
            return inventory_service.serialize_many(db, recent)

        tokens = _extract_meaningful_tokens(cleaned)
        if not tokens:
            return []
        descendant_loc_ids = location_repository.search_matching_and_descendant_ids(db, tokens)
        items = item_repository.search_items(db, tokens=tokens, descendant_location_ids=descendant_loc_ids, limit=limit)
        return inventory_service.serialize_many(db, items)

    @classmethod
    async def search_by_photo(cls, db: Session, file: UploadFile, limit: int = 20) -> dict:
        raw_bytes = await file.read()
        if not raw_bytes:
            raise BadRequestError("Please choose a valid photo to search.")
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                img.verify()
        except (UnidentifiedImageError, OSError):
            raise BadRequestError("Uploaded file is not a valid image.")

        gemini_key = os.environ.get("GEMINI_API_KEY") or settings.GEMINI_API_KEY
        extracted_query = ""
        if gemini_key:
            try:
                import httpx

                b64_data = base64.b64encode(raw_bytes).decode("utf-8")
                prompt = (
                    "Identify the main tool, part, device, or household item in this photo. "
                    'Return JSON: {"query": "short 1-3 word search phrase"}'
                )
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"gemini-2.5-flash:generateContent?key={gemini_key}"
                )
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"text": prompt},
                                {"inline_data": {"mime_type": file.content_type or "image/jpeg", "data": b64_data}},
                            ]
                        }
                    ],
                    "generationConfig": {"response_mime_type": "application/json"},
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    extracted_query = str(json.loads(text).get("query", "")).strip()
            except Exception:
                extracted_query = ""

        if not extracted_query:
            extracted_query = _keywords_from_filename(file.filename)

        items = cls.search(db, query=extracted_query, limit=limit) if extracted_query else []
        return {
            "query": extracted_query,
            "total": len(items),
            "items": items,
            "has_vision_api": bool(gemini_key),
        }


search_service = SearchService()
