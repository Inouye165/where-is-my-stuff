from typing import List, Literal, Optional
from urllib.parse import urlsplit
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
    source_photo_ids: Optional[List[int]] = Field(default=None, max_length=50)
    source_attachment_path: Optional[str] = Field(default=None, max_length=300)
    source_attachment_filename: Optional[str] = Field(default=None, max_length=255)
    source_attachment_title: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=80)
    items: Optional[List[ItemCreate]] = Field(default=None, max_length=500)
    raw_lines: Optional[str] = Field(default=None, max_length=50_000)
    raw_text: Optional[str] = Field(default=None, max_length=50_000)

    @field_validator("assortment_photo", "source_attachment_path")
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


class ItemPhotoAttachmentCreate(BaseModel):
    photo_path: str = Field(..., max_length=300)
    thumbnail_path: Optional[str] = Field(default=None, max_length=300)
    title: Optional[str] = Field(default=None, max_length=200)
    set_as_main: bool = False

    @field_validator("photo_path", "thumbnail_path")
    @classmethod
    def check_photo_paths(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


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
    source_attachment_path: Optional[str] = None
    source_attachment_filename: Optional[str] = None
    source_attachment_size_bytes: Optional[int] = None


class BundleSavedImage(BaseModel):
    file_path: str
    thumbnail_path: Optional[str] = None
    filename: str


class BundleSavedDocument(BaseModel):
    file_path: str
    filename: str
    attachment_type: str = "document"
    size_bytes: int = 0
    title: Optional[str] = None


class ItemBundleAnalysisResponse(BaseModel):
    status: str = "fallback"
    name: Optional[str] = None
    part_number: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = "pcs"
    tags: Optional[str] = None
    specs: Optional[str] = None
    use_case: Optional[str] = None
    setup_info: Optional[str] = None
    reference_links: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    saved_images: List[BundleSavedImage] = Field(default_factory=list)
    saved_documents: List[BundleSavedDocument] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    provider_used: Optional[str] = None
    model_used: Optional[str] = None
    message: str = ""


class ItemBundleDocumentRef(BaseModel):
    file_path: str = Field(..., max_length=300)
    filename: Optional[str] = Field(default=None, max_length=255)
    title: Optional[str] = Field(default=None, max_length=200)

    @field_validator("file_path")
    @classmethod
    def check_doc_path(cls, v: str) -> str:
        res = validate_safe_photo_ref(v)
        if not res:
            raise ValueError("Document file_path is required.")
        return res


class ItemFromBundleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    location_id: Optional[int] = Field(default=None, ge=1)
    quantity: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    unit: Optional[str] = Field(default="pcs", max_length=30)
    category: Optional[str] = Field(default=None, max_length=80)
    serial_or_part_number: Optional[str] = Field(default=None, max_length=120)
    tags: Optional[str] = Field(default=None, max_length=300)
    specs: Optional[str] = Field(default=None, max_length=8000)
    use_case: Optional[str] = Field(default=None, max_length=6000)
    setup_info: Optional[str] = Field(default=None, max_length=8000)
    reference_links: List[str] = Field(default_factory=list, max_length=20)
    description: Optional[str] = Field(default=None, max_length=20_000)
    main_photo_path: Optional[str] = Field(default=None, max_length=300)
    image_paths: List[str] = Field(default_factory=list, max_length=50)
    documents: List[ItemBundleDocumentRef] = Field(default_factory=list, max_length=30)

    @field_validator("reference_links")
    @classmethod
    def check_reference_links(cls, v: List[str]) -> List[str]:
        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in v:
            if not isinstance(raw, str):
                raise ValueError("Reference link must be a string.")
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
                raise ValueError("Reference link contains invalid control characters.")
            link = raw.strip()
            if not link:
                continue
            if len(link) > 2048 or any(ch.isspace() for ch in link):
                raise ValueError("Reference link is invalid or exceeds 2048 characters.")
            parsed = urlsplit(link)
            if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
                raise ValueError("Reference links must use http:// or https://.")
            if link not in seen:
                seen.add(link)
                cleaned.append(link)
        return cleaned

    @field_validator("name")
    @classmethod
    def check_bundle_name(cls, v: Optional[str]) -> Optional[str]:
        return validate_name(v)

    @field_validator("main_photo_path")
    @classmethod
    def check_main_photo(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)

    @field_validator("image_paths")
    @classmethod
    def check_image_paths(cls, v: List[str]) -> List[str]:
        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in v:
            safe = validate_safe_photo_ref(raw)
            if safe and safe not in seen:
                seen.add(safe)
                cleaned.append(safe)
        return cleaned
