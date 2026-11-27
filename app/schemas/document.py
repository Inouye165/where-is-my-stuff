from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.common import validate_safe_photo_ref
from app.schemas.item import ItemAttachmentRead


def _validate_quad_points(quad: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
    if quad is None:
        return None
    if len(quad) != 4:
        raise ValueError("quad must contain exactly 4 [x, y] corner points.")
    cleaned: List[List[float]] = []
    for pt in quad:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError("Each quad corner must be [x, y].")
        x = float(pt[0])
        y = float(pt[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("Quad corner coordinates must be normalized between 0.0 and 1.0.")
        cleaned.append([x, y])
    return cleaned


class ScannerDeviceInfo(BaseModel):
    id: str
    name: str
    manufacturer: Optional[str] = None
    description: Optional[str] = None
    backend: str


class ScannerListResponse(BaseModel):
    available: bool
    platform: str
    backend: str
    devices: List[ScannerDeviceInfo] = Field(default_factory=list)
    message: Optional[str] = None


class ScannerScanRequest(BaseModel):
    device_id: Optional[str] = Field(default=None, max_length=256)
    dpi: int = Field(default=300, ge=75, le=600)
    color_mode: Literal["color", "grayscale", "bw"] = "color"
    source: Literal["flatbed", "feeder"] = "flatbed"


class ScannerScanResponse(BaseModel):
    original_path: str
    thumbnail_path: str
    filename: str
    width: int
    height: int
    scanner_name: str


class DocumentDetectCornersRequest(BaseModel):
    photo_path: str = Field(..., min_length=1, max_length=300)

    @field_validator("photo_path")
    @classmethod
    def check_photo_path(cls, v: str) -> str:
        safe = validate_safe_photo_ref(v)
        if not safe:
            raise ValueError("Valid photo_path is required.")
        return safe


class DocumentDetectCornersResponse(BaseModel):
    quad: List[List[float]]
    detected: bool
    confidence: float
    skew_angle_deg: float
    width: int
    height: int
    suggested_aspect: str


class DocumentEnhanceOptions(BaseModel):
    preset: Literal["auto_document", "clean_bw", "grayscale_scan", "photo_preserve"] = "auto_document"
    auto_perspective: bool = True
    quad: Optional[List[List[float]]] = None
    auto_orient: bool = False
    auto_deskew: bool = True
    rotation_deg: Literal[0, 90, 180, 270] = 0
    fine_angle_deg: float = Field(default=0.0, ge=-20.0, le=20.0)
    aspect_mode: Literal["auto", "letter", "a4", "legal", "square", "original"] = "auto"
    remove_shading: bool = True
    shading_strength: float = Field(default=0.85, ge=0.0, le=1.0)
    denoise_strength: float = Field(default=0.45, ge=0.0, le=1.0)
    sharpen_strength: float = Field(default=0.4, ge=0.0, le=1.0)
    white_balance: bool = True
    max_dimension: int = Field(default=4096, ge=800, le=4096)

    @field_validator("quad")
    @classmethod
    def check_quad(cls, v: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        return _validate_quad_points(v)


class DocumentEnhanceRequest(BaseModel):
    photo_path: str = Field(..., min_length=1, max_length=300)
    options: DocumentEnhanceOptions = Field(default_factory=DocumentEnhanceOptions)

    @field_validator("photo_path")
    @classmethod
    def check_photo_path(cls, v: str) -> str:
        safe = validate_safe_photo_ref(v)
        if not safe:
            raise ValueError("Valid photo_path is required.")
        return safe


class DocumentEnhanceResponse(BaseModel):
    original_path: str
    enhanced_path: str
    thumbnail_path: str
    width: int
    height: int
    applied_perspective: bool
    applied_rotation_deg: int = 0
    applied_deskew_deg: float
    detected_quad: Optional[List[List[float]]] = None
    warnings: List[str] = Field(default_factory=list)


class DocumentPageInput(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=300)
    enhanced_path: Optional[str] = Field(default=None, max_length=300)
    enhance_options: Optional[DocumentEnhanceOptions] = None

    @field_validator("source_path")
    @classmethod
    def check_source_path(cls, v: str) -> str:
        safe = validate_safe_photo_ref(v)
        if not safe:
            raise ValueError("Valid source_path is required.")
        return safe

    @field_validator("enhanced_path")
    @classmethod
    def check_enhanced_path(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class DocumentSaveRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=160)
    pages: List[DocumentPageInput] = Field(..., min_length=1, max_length=50)
    save_enhanced_pdf: bool = True
    save_typeset_pdf: bool = True
    typeset_mode: Literal["layout_matched", "clean_reflow"] = "layout_matched"
    include_graphics: bool = True
    auto_enhance_unprocessed: bool = True
    pdf_dpi: int = Field(default=200, ge=120, le=300)

    @model_validator(mode="after")
    def validate_pdf_selection(self) -> "DocumentSaveRequest":
        if not self.save_enhanced_pdf and not self.save_typeset_pdf:
            raise ValueError("Select at least one PDF output (Enhanced Scan PDF or Typeset PDF).")
        return self


class DocumentSaveResponse(BaseModel):
    enhanced_pdf: Optional[ItemAttachmentRead] = None
    typeset_pdf: Optional[ItemAttachmentRead] = None
    page_count: int
    ocr_word_count: int
    ocr_available: bool
    warnings: List[str] = Field(default_factory=list)


class AttachmentTypesetRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=160)
    typeset_mode: Literal["layout_matched", "clean_reflow"] = "layout_matched"
    include_graphics: bool = True
    enhance_first: bool = True


class AttachmentRotateRequest(BaseModel):
    rotation_deg: Literal[90, 180, 270]


class DocumentTypesetPreviewRequest(BaseModel):
    photo_path: str = Field(..., min_length=1, max_length=300)
    typeset_mode: Literal["layout_matched", "clean_reflow"] = "layout_matched"
    include_graphics: bool = True

    @field_validator("photo_path")
    @classmethod
    def check_photo_path(cls, v: str) -> str:
        safe = validate_safe_photo_ref(v)
        if not safe:
            raise ValueError("Valid photo_path is required.")
        return safe


class DocumentStitchPatchRequest(BaseModel):
    base_photo_path: str = Field(..., min_length=1, max_length=300)
    patch_photo_path: str = Field(..., min_length=1, max_length=300)
    enhance_patch: bool = True
    max_dimension: int = Field(default=4096, ge=1000, le=4096)

    @field_validator("base_photo_path", "patch_photo_path")
    @classmethod
    def check_paths(cls, v: str) -> str:
        safe = validate_safe_photo_ref(v)
        if not safe:
            raise ValueError("Valid upload photo path is required.")
        return safe


class DocumentStitchPatchResponse(BaseModel):
    stitched_path: str
    thumbnail_path: str
    width: int
    height: int
    inliers: int
    match_confidence: float
    patch_bbox_norm: List[float]
    upscale_factor: float
