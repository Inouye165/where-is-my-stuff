import io
from pathlib import Path
import re
from typing import List, Optional, Set
import anyio
from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import session_scope
from app.core.exceptions import BadRequestError, PayloadTooLargeError
from app.models.item import Item
from app.repositories.item_repository import item_repository
from app.repositories.location_repository import location_repository
from app.schemas.item import ItemRead
from app.services.inventory_service import inventory_service
from app.services.vision_service import vision_service

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


def _split_query_tokens(query: str) -> tuple[List[str], List[str]]:
    """Return (primary tokens typed by the user, synonym expansions)."""
    raw = re.findall(r"[a-zA-Z0-9\-_.]+", query.lower())
    primary = [t for t in raw if t not in _STOP_WORDS and len(t) > 1]
    if not primary:
        primary = [t for t in raw if len(t) > 0]
    primary = list(dict.fromkeys(primary))
    seen: Set[str] = set(primary)
    synonyms: List[str] = []
    for tok in primary:
        for syn in _PLAIN_SYNONYMS.get(tok, ()):
            if syn not in seen:
                seen.add(syn)
                synonyms.append(syn)
    return primary, synonyms


def _extract_meaningful_tokens(query: str) -> List[str]:
    primary, synonyms = _split_query_tokens(query)
    return primary + synonyms


_FIELD_WEIGHTS = (
    ("name", 10.0),
    ("serial_or_part_number", 8.0),
    ("category", 4.0),
    ("tags", 4.0),
    ("description", 2.0),
)
_SYNONYM_FACTOR = 0.3
_LOCATION_MATCH_SCORE = 3.0
_ALL_PRIMARY_BONUS = 5.0
_CANDIDATE_POOL = 2000


def _relevance(
    item: Item,
    primary: List[str],
    synonym_patterns: List[re.Pattern[str]],
    matched_location_ids: Set[int],
) -> float:
    fields = [((getattr(item, attr) or "").lower(), weight) for attr, weight in _FIELD_WEIGHTS]
    score = 0.0
    primary_hits = 0
    # Typed terms match anywhere ("4056" -> "TP4056"); short synonyms only at a word start
    # so "led" doesn't score on "soldered".
    for tok in primary:
        tok_score = sum(weight for text, weight in fields if tok in text)
        if tok_score:
            primary_hits += 1
        score += tok_score
    for word_start in synonym_patterns:
        score += _SYNONYM_FACTOR * sum(weight for text, weight in fields if word_start.search(text))
    if primary and primary_hits == len(primary):
        score += _ALL_PRIMARY_BONUS
    if item.location_id in matched_location_ids:
        score += _LOCATION_MATCH_SCORE
    return score


def _keywords_from_filename(filename: Optional[str]) -> str:
    if not filename:
        return ""
    stem = Path(filename).stem
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

        primary, synonyms = _split_query_tokens(cleaned)
        tokens = primary + synonyms
        if not tokens:
            return []
        descendant_loc_ids = location_repository.search_matching_and_descendant_ids(db, tokens)
        candidates = item_repository.search_items(
            db, tokens=tokens, descendant_location_ids=descendant_loc_ids, limit=max(limit, _CANDIDATE_POOL)
        )
        synonym_patterns = [re.compile(rf"(?<![a-z0-9]){re.escape(tok)}") for tok in synonyms]
        # Candidates arrive newest-first; sorted() is stable, so ties keep that order.
        ranked = sorted(
            candidates,
            key=lambda it: _relevance(it, primary, synonym_patterns, descendant_loc_ids),
            reverse=True,
        )
        return inventory_service.serialize_many(db, ranked[:limit])

    @classmethod
    async def search_by_photo(cls, db: Session, file: UploadFile, limit: int = 20) -> dict:
        bind = db.get_bind()
        max_bytes = int(settings.MAX_IMAGE_UPLOAD_SIZE_MB) * 1024 * 1024
        raw_bytes = await file.read(max_bytes + 1)
        if not raw_bytes:
            raise BadRequestError("Please choose a valid photo to search.")
        if len(raw_bytes) > max_bytes:
            raise PayloadTooLargeError(
                f"Image upload exceeds maximum allowed size of {settings.MAX_IMAGE_UPLOAD_SIZE_MB} MB."
            )

        def _verify_image() -> None:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                img.verify()

        try:
            await anyio.to_thread.run_sync(_verify_image)
        except (UnidentifiedImageError, OSError) as exc:
            raise BadRequestError("Uploaded file is not a valid image.") from exc

        extracted_query, has_vision_api = await vision_service.extract_search_query(
            raw_bytes,
            mime_type=file.content_type or "image/jpeg",
        )
        if not extracted_query:
            extracted_query = _keywords_from_filename(file.filename)

        def _run_search() -> List[ItemRead]:
            with session_scope(bind) as thread_db:
                return cls.search(thread_db, extracted_query, limit)

        items = await anyio.to_thread.run_sync(_run_search) if extracted_query else []
        return {
            "query": extracted_query,
            "total": len(items),
            "items": items,
            "has_vision_api": has_vision_api,
        }


search_service = SearchService()
