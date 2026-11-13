from enum import Enum
import re
from typing import Any, Optional
from pydantic import BaseModel

SAFE_UPLOAD_URL_RE = re.compile(r"^/uploads/[a-zA-Z0-9._/\-]+$")


class LocationType(str, Enum):
    site = "site"
    room = "room"
    shelf = "shelf"
    container = "container"
    bin = "bin"
    drawer = "drawer"
    other = "other"


class ItemStatus(str, Enum):
    stored = "stored"
    in_use = "in_use"


class ItemSortOrder(str, Enum):
    last_seen_desc = "last_seen_desc"
    last_seen_asc = "last_seen_asc"
    name_asc = "name_asc"
    name_desc = "name_desc"
    qty_desc = "qty_desc"
    updated_desc = "updated_desc"


def validate_safe_photo_ref(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 300:
        raise ValueError("Photo path exceeds maximum length of 300 characters.")
    if ".." in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise ValueError("Invalid path traversal sequence in photo URL.")
    if not SAFE_UPLOAD_URL_RE.match(cleaned):
        raise ValueError("Photo reference must be a local '/uploads/...' path.")
    return cleaned


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    detail: Any
    error: ErrorDetail

