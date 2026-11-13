from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.audit import AuditLogRead
from app.schemas.common import ItemStatus, validate_safe_photo_ref
from app.schemas.location import Breadcrumb


class ItemBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    category: Optional[str] = Field(default=None, max_length=80)
    tags: Optional[str] = Field(default=None, max_length=300)
    quantity: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    unit: Optional[str] = Field(default="pcs", max_length=30)
    serial_or_part_number: Optional[str] = Field(default=None, max_length=120)
    item_photo: Optional[str] = Field(default=None, max_length=300)
    location_id: Optional[int] = Field(default=None, ge=1)
    status: Optional[ItemStatus] = Field(default=ItemStatus.stored)
    status_note: Optional[str] = Field(default=None, max_length=250)

    @field_validator("item_photo")
    @classmethod
    def check_item_photo(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class ItemCreate(ItemBase):
    pass


class ItemBulkCreate(BaseModel):
    location_id: int = Field(..., ge=1)
    category: Optional[str] = Field(default=None, max_length=80)
    items: Optional[List[ItemCreate]] = None
    raw_lines: Optional[str] = Field(default=None, max_length=50_000)
    raw_text: Optional[str] = Field(default=None, max_length=50_000)


class ItemUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    category: Optional[str] = Field(default=None, max_length=80)
    tags: Optional[str] = Field(default=None, max_length=300)
    quantity: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    unit: Optional[str] = Field(default=None, max_length=30)
    serial_or_part_number: Optional[str] = Field(default=None, max_length=120)
    item_photo: Optional[str] = Field(default=None, max_length=300)
    location_id: Optional[int] = Field(default=None, ge=1)
    status: Optional[ItemStatus] = None
    status_note: Optional[str] = Field(default=None, max_length=250)

    @field_validator("item_photo")
    @classmethod
    def check_item_photo(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class LocationPhotos(BaseModel):
    container_photo: Optional[str] = None
    context_photo: Optional[str] = None


class ItemRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = "pcs"
    serial_or_part_number: Optional[str] = None
    item_photo: Optional[str] = None
    location_id: Optional[int] = None
    status: str = "stored"
    status_note: Optional[str] = None
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    location_name: Optional[str] = None
    location_full_path: Optional[str] = None
    location_breadcrumbs: List[Breadcrumb] = Field(default_factory=list)
    location_photos: LocationPhotos = Field(default_factory=LocationPhotos)

    model_config = ConfigDict(from_attributes=True)


class ItemDetailRead(ItemRead):
    audit_logs: List[AuditLogRead] = Field(default_factory=list)


class DetectedItem(BaseModel):
    name: str
    category: Optional[str] = None
    estimated_qty: Optional[int] = 1
    brief_specs: Optional[str] = None


class VisionAnalysisResponse(BaseModel):
    status: str = "fallback"
    items: List[DetectedItem] = Field(default_factory=list)
    has_vision_api: bool = False
    message: str = ""
    instructions: str = "Snap an open drawer photo or type items directly into the quick-entry box."
