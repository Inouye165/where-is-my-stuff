from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.common import LocationType, validate_safe_photo_ref


class Breadcrumb(BaseModel):
    id: int
    name: str
    location_type: str


class AutoGenerateChildren(BaseModel):
    prefix: str = Field(default="Drawer", min_length=1, max_length=60)
    start: int = Field(default=1, ge=0, le=500)
    end: int = Field(default=5, ge=0, le=500)
    child_type: LocationType = Field(default=LocationType.bin)


class LocationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    location_type: LocationType = Field(default=LocationType.room)
    parent_id: Optional[int] = Field(default=None, ge=1)
    container_photo: Optional[str] = Field(default=None, max_length=300)
    context_photo: Optional[str] = Field(default=None, max_length=300)
    grid_rows: Optional[int] = Field(default=None, ge=1, le=26)
    grid_cols: Optional[int] = Field(default=None, ge=1, le=26)
    grid_config: Optional[str] = Field(default=None, max_length=4000)
    grid_row: Optional[int] = Field(default=None, ge=1, le=26)
    grid_col: Optional[int] = Field(default=None, ge=1, le=26)
    grid_row_span: Optional[int] = Field(default=None, ge=1, le=26)
    grid_col_span: Optional[int] = Field(default=None, ge=1, le=26)

    @field_validator("container_photo", "context_photo")
    @classmethod
    def check_photo_paths(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class LocationCreate(LocationBase):
    auto_generate_children: Optional[AutoGenerateChildren] = None


class LocationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    location_type: Optional[LocationType] = None
    parent_id: Optional[int] = Field(default=None, ge=1)
    container_photo: Optional[str] = Field(default=None, max_length=300)
    context_photo: Optional[str] = Field(default=None, max_length=300)
    grid_rows: Optional[int] = Field(default=None, ge=1, le=26)
    grid_cols: Optional[int] = Field(default=None, ge=1, le=26)
    grid_config: Optional[str] = Field(default=None, max_length=4000)
    grid_row: Optional[int] = Field(default=None, ge=1, le=26)
    grid_col: Optional[int] = Field(default=None, ge=1, le=26)
    grid_row_span: Optional[int] = Field(default=None, ge=1, le=26)
    grid_col_span: Optional[int] = Field(default=None, ge=1, le=26)

    @field_validator("container_photo", "context_photo")
    @classmethod
    def check_photo_paths(cls, v: Optional[str]) -> Optional[str]:
        return validate_safe_photo_ref(v)


class LocationRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    location_type: str
    parent_id: Optional[int] = None
    container_photo: Optional[str] = None
    context_photo: Optional[str] = None
    grid_rows: Optional[int] = None
    grid_cols: Optional[int] = None
    grid_config: Optional[str] = None
    grid_row: Optional[int] = None
    grid_col: Optional[int] = None
    grid_row_span: Optional[int] = None
    grid_col_span: Optional[int] = None
    full_path: str
    breadcrumbs: List[Breadcrumb] = Field(default_factory=list)
    item_count: int = 0
    total_item_count: int = 0
    child_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GridSlotSpec(BaseModel):
    id: Optional[int] = Field(default=None, ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    location_type: LocationType = Field(default=LocationType.drawer)
    grid_row: int = Field(..., ge=1, le=26)
    grid_col: int = Field(..., ge=1, le=26)
    grid_row_span: int = Field(default=1, ge=1, le=26)
    grid_col_span: int = Field(default=1, ge=1, le=26)
    merge_from_ids: List[int] = Field(default_factory=list)


class LocationGridSave(BaseModel):
    grid_rows: int = Field(..., ge=1, le=26)
    grid_cols: int = Field(..., ge=1, le=26)
    grid_config: Optional[str] = Field(default=None, max_length=4000)
    slots: List[GridSlotSpec] = Field(default_factory=list)


class LocationTree(LocationRead):
    children: List["LocationTree"] = Field(default_factory=list)


LocationTree.model_rebuild()
