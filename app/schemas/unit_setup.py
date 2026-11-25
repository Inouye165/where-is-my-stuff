from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from app.schemas.common import LocationType, validate_name
from app.schemas.location import LocationPhotoRead, LocationRead

UnitSetupStatus = Literal[
    "waiting_for_phone",
    "creating_unit",
    "capturing_drawers",
    "ready_for_review",
    "completed",
    "expired",
]

UnitSetupActiveStatus = Literal[
    "waiting_for_phone",
    "creating_unit",
    "capturing_drawers",
    "ready_for_review",
    "completed",
]


class UnitSetupSessionCreate(BaseModel):
    room_id: Optional[int] = Field(default=None, ge=1)
    base_url: Optional[str] = Field(default=None, max_length=300)


class UnitSetupCreateUnitRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    label: Optional[str] = Field(default=None, max_length=40)
    unit_kind: str = Field(default="dresser", min_length=1, max_length=40)
    compartment_count: int = Field(default=5, ge=0, le=50)
    compartment_label: str = Field(default="Drawer", min_length=1, max_length=40)
    compartment_type: LocationType = Field(default=LocationType.drawer)
    grid_cols: int = Field(default=1, ge=1, le=10)
    description: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return validate_name(v) or ""


class UnitSetupProgressUpdate(BaseModel):
    current_drawer_index: Optional[int] = Field(default=None, ge=0, le=50)
    status: Optional[UnitSetupActiveStatus] = None


class UnitSetupDrawerSummary(BaseModel):
    id: int
    name: str
    location_type: str
    grid_row: Optional[int] = None
    grid_col: Optional[int] = None
    photo_count: int = 0
    item_count: int = 0
    photos: List[LocationPhotoRead] = Field(default_factory=list)


class UnitSetupSessionCreateResponse(BaseModel):
    token: str
    room_id: Optional[int] = None
    room_name: Optional[str] = None
    status: UnitSetupStatus
    expires_in_seconds: int
    mobile_url: str
    qr_url: str


class UnitSetupSessionStatusResponse(BaseModel):
    token: str
    status: UnitSetupStatus
    room_id: Optional[int] = None
    room_name: Optional[str] = None
    unit_id: Optional[int] = None
    unit_name: Optional[str] = None
    current_drawer_index: int = 0
    expires_in_seconds: int
    unit: Optional[LocationRead] = None
    drawers: List[UnitSetupDrawerSummary] = Field(default_factory=list)
