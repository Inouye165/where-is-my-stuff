from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.audit import AuditLogRead
from app.schemas.common import ItemStatus, UTCDateTime, validate_name, validate_safe_photo_ref
from app.schemas.location import Breadcrumb, LocationRead


class ItemBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=20_000)
    category: Optional[str] = Field(default=None, max_length=80)
    tags: Optional[str] = Field(default=None, max_length=300)
    quantity: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    unit: Optional[str] = Field(default="pcs", max_length=30)
    serial_or_part_number: Optional[str] = Field(default=None, max_length=120)
    item_photo: Optional[str] = Field(default=None, max_length=300)
    location_id: Optional[int] = Field(default=None, ge=1)
    status: Optional[ItemStatus] = Field(default=ItemStatus.stored)
    status_note: Optional[str] = Field(default=None, max_length=250)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: Optional[str]) -> Optional[str]:
        return validate_name(v)

    @field_validator("item_photo")
    @classmethod
    def check_item_photo(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class ItemCreate(ItemBase):
    pass


class ItemBulkCreate(BaseModel):
    location_id: int = Field(..., ge=1)
    save_mode: Literal["separate", "box", "assortment"] = "separate"
    assortment_name: Optional[str] = Field(default=None, max_length=160)
    assortment_label: Optional[str] = Field(default=None, max_length=40)
    assortment_photo: Optional[str] = Field(default=None, max_length=300)
    source_photo_ids: Optional[List[int]] = None
    category: Optional[str] = Field(default=None, max_length=80)
    items: Optional[List[ItemCreate]] = None
    raw_lines: Optional[str] = Field(default=None, max_length=50_000)
    raw_text: Optional[str] = Field(default=None, max_length=50_000)

    @field_validator("assortment_photo")
    @classmethod
    def check_assortment_photo(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class ItemGroupIntoBoxRequest(BaseModel):
    parent_location_id: int = Field(..., ge=1)
    box_name: Optional[str] = Field(default=None, max_length=120)
    target_box_id: Optional[int] = Field(default=None, ge=1)
    item_ids: List[int] = Field(..., min_length=1, max_length=1000)
    box_photo: Optional[str] = Field(default=None, max_length=300)

    @field_validator("box_photo")
    @classmethod
    def check_box_photo(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class ItemGroupIntoBoxResponse(BaseModel):
    box: LocationRead
    moved_count: int


class ItemConvertToBoxRequest(BaseModel):
    box_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    box_label: Optional[str] = Field(default=None, max_length=40)
    keep_item_inside: bool = False


class ItemUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=20_000)
    category: Optional[str] = Field(default=None, max_length=80)
    tags: Optional[str] = Field(default=None, max_length=300)
    quantity: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    unit: Optional[str] = Field(default=None, max_length=30)
    serial_or_part_number: Optional[str] = Field(default=None, max_length=120)
    item_photo: Optional[str] = Field(default=None, max_length=300)
    location_id: Optional[int] = Field(default=None, ge=1)
    status: Optional[ItemStatus] = None
    status_note: Optional[str] = Field(default=None, max_length=250)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: Optional[str]) -> Optional[str]:
        return validate_name(v)

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
    last_seen_at: UTCDateTime
    created_at: UTCDateTime
    updated_at: UTCDateTime
    location_name: Optional[str] = None
    location_full_path: Optional[str] = None
    location_breadcrumbs: List[Breadcrumb] = Field(default_factory=list)
    location_photos: LocationPhotos = Field(default_factory=LocationPhotos)

    model_config = ConfigDict(from_attributes=True)


class ItemPageResponse(BaseModel):
    items: List[ItemRead] = Field(default_factory=list)
    total: int
    limit: int
    next_cursor: Optional[str] = None
    has_more: bool = False


class ItemAttachmentRead(BaseModel):
    id: int
    item_id: int
    file_path: str
    thumbnail_path: Optional[str] = None
    filename: str
    content_type: Optional[str] = None
    attachment_type: str
    title: Optional[str] = None
    size_bytes: int = 0
    created_at: UTCDateTime

    model_config = ConfigDict(from_attributes=True)


class ItemImagesToPdfRequest(BaseModel):
    photo_paths: List[str] = Field(..., min_length=1, max_length=50)
    title: Optional[str] = Field(default=None, max_length=160)
    max_dimension: int = Field(default=1400, ge=400, le=2400)
    remove_source_photos: bool = False

    @field_validator("photo_paths")
    @classmethod
    def check_photo_paths(cls, v: List[str]) -> List[str]:
        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in v:
            safe = validate_safe_photo_ref(raw)
            if safe and safe not in seen:
                seen.add(safe)
                cleaned.append(safe)
        if not cleaned:
            raise ValueError("At least one valid photo path is required.")
        return cleaned


class ItemDetailRead(ItemRead):
    audit_logs: List[AuditLogRead] = Field(default_factory=list)
    attachments: List[ItemAttachmentRead] = Field(default_factory=list)


class DetectedItem(BaseModel):
    name: str
    part_number: Optional[str] = None
    category: Optional[str] = None
    estimated_qty: Optional[int] = 1
    brief_specs: Optional[str] = None
    bbox: Optional[List[float]] = None
    crop_photo_path: Optional[str] = None


class FindItemImageResponse(BaseModel):
    status: str = "ok"
    item_photo: str
    item: ItemRead


class VisionAnalysisResponse(BaseModel):
    status: str = "fallback"
    items: List[DetectedItem] = Field(default_factory=list)
    suggested_group_name: Optional[str] = None
    suggested_category: Optional[str] = None
    has_vision_api: bool = False
    provider_used: Optional[str] = None
    model_used: Optional[str] = None
    message: str = ""
    instructions: str = "Snap an open drawer photo or type items directly into the quick-entry box."
