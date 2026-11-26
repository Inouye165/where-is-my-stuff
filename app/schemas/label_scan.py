from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from app.schemas.common import validate_safe_photo_ref

ConfidenceTier = Literal["green", "yellow", "red", "unlabeled"]
LabelSource = Literal["ocr", "ollama", "hybrid", "quality_gate", "empty"]


class LabelScanSessionCreate(BaseModel):
    container_id: int = Field(..., ge=1)
    base_url: Optional[str] = Field(default=None, max_length=300)


class LabelScanSessionCreateResponse(BaseModel):
    token: str
    container_id: int
    container_name: str
    status: str
    expires_in_seconds: int
    mobile_url: str
    qr_url: str


class CloseupZone(BaseModel):
    zone_id: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=80)
    min_row: int = Field(..., ge=1, le=26)
    max_row: int = Field(..., ge=1, le=26)
    min_col: int = Field(..., ge=1, le=26)
    max_col: int = Field(..., ge=1, le=26)
    grid_rows: int = Field(..., ge=1, le=26)
    grid_cols: int = Field(..., ge=1, le=26)
    slot_coords: List[str] = Field(default_factory=list)
    slot_count: int = Field(default=0, ge=0)
    photo_path: Optional[str] = Field(default=None, max_length=300)

    @field_validator("photo_path")
    @classmethod
    def check_photo_path(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class CellLabelResult(BaseModel):
    coord: str = Field(..., min_length=1, max_length=24)
    slot_id: Optional[int] = Field(default=None, ge=1)
    grid_row: int = Field(..., ge=1, le=26)
    grid_col: int = Field(..., ge=1, le=26)
    grid_row_span: int = Field(default=1, ge=1, le=26)
    grid_col_span: int = Field(default=1, ge=1, le=26)
    existing_name: Optional[str] = Field(default=None, max_length=120)
    has_custom_existing_name: bool = False
    proposed_name: str = Field(default="", max_length=120)
    proposed_description: Optional[str] = Field(default=None, max_length=2000)
    raw_text: str = Field(default="", max_length=2000)
    confidence: int = Field(default=0, ge=0, le=100)
    confidence_tier: ConfidenceTier = "unlabeled"
    reason: Optional[str] = Field(default=None, max_length=240)
    crop_photo_path: Optional[str] = Field(default=None, max_length=300)
    source: LabelSource = "empty"

    @field_validator("crop_photo_path")
    @classmethod
    def check_crop_path(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class LabelScanSummary(BaseModel):
    total: int = 0
    green: int = 0
    yellow: int = 0
    red: int = 0
    unlabeled: int = 0


class LabelScanSessionStatusResponse(BaseModel):
    token: str
    container_id: int
    container_name: str
    status: str
    expires_in_seconds: int
    overview_photo_path: Optional[str] = None
    active_zone: Optional[CloseupZone] = None
    closeup_photos: List[dict] = Field(default_factory=list)
    results: List[CellLabelResult] = Field(default_factory=list)
    closeup_zones: List[CloseupZone] = Field(default_factory=list)
    summary: LabelScanSummary = Field(default_factory=LabelScanSummary)


class QuadPoint(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


class LabelScanAnalyzeRequest(BaseModel):
    photo_path: str = Field(..., min_length=1, max_length=300)
    quad: List[QuadPoint] = Field(..., min_length=4, max_length=4)
    grid_rows: Optional[int] = Field(default=None, ge=1, le=26)
    grid_cols: Optional[int] = Field(default=None, ge=1, le=26)
    zone_id: Optional[str] = Field(default=None, max_length=40)
    min_row: Optional[int] = Field(default=None, ge=1, le=26)
    max_row: Optional[int] = Field(default=None, ge=1, le=26)
    min_col: Optional[int] = Field(default=None, ge=1, le=26)
    max_col: Optional[int] = Field(default=None, ge=1, le=26)
    row_boundaries: Optional[List[float]] = Field(default=None, min_length=2, max_length=27)
    col_boundaries: Optional[List[float]] = Field(default=None, min_length=2, max_length=27)
    provider: Optional[str] = Field(default=None, max_length=32)
    model: Optional[str] = Field(default=None, max_length=128)
    use_ollama_refinement: bool = True

    @field_validator("photo_path")
    @classmethod
    def check_photo_path(cls, v: str) -> str:
        validated = validate_safe_photo_ref(v)
        if not validated:
            raise ValueError("photo_path is required.")
        return validated


class LabelScanAnalyzeResponse(BaseModel):
    status: str
    engine_used: str
    results: List[CellLabelResult] = Field(default_factory=list)
    closeup_zones: List[CloseupZone] = Field(default_factory=list)
    summary: LabelScanSummary = Field(default_factory=LabelScanSummary)
    message: str


class LabelScanCloseupRequest(BaseModel):
    clear: bool = False
    zone_id: Optional[str] = Field(default=None, max_length=40)
    min_row: Optional[int] = Field(default=None, ge=1, le=26)
    max_row: Optional[int] = Field(default=None, ge=1, le=26)
    min_col: Optional[int] = Field(default=None, ge=1, le=26)
    max_col: Optional[int] = Field(default=None, ge=1, le=26)
    slot_coords: List[str] = Field(default_factory=list)


class LabelApplySlotUpdate(BaseModel):
    slot_id: Optional[int] = Field(default=None, ge=1)
    grid_row: Optional[int] = Field(default=None, ge=1, le=26)
    grid_col: Optional[int] = Field(default=None, ge=1, le=26)
    grid_row_span: int = Field(default=1, ge=1, le=26)
    grid_col_span: int = Field(default=1, ge=1, le=26)
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    create_starter_item: bool = False

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned


class LabelScanApplyRequest(BaseModel):
    updates: List[LabelApplySlotUpdate] = Field(default_factory=list, max_length=256)
    overwrite_existing: bool = False
    save_grid_rows: Optional[int] = Field(default=None, ge=1, le=26)
    save_grid_cols: Optional[int] = Field(default=None, ge=1, le=26)


class LabelScanApplyResponse(BaseModel):
    status: str
    updated_count: int
    created_slots_count: int
    created_items_count: int
    skipped_existing_count: int
