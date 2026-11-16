from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.common import UTCDateTime


class AuditLogRead(BaseModel):
    id: int
    item_id: int
    location_id: Optional[int] = None
    action: str
    notes: Optional[str] = None
    recorded_at: UTCDateTime

    model_config = ConfigDict(from_attributes=True)


class AuditVerifyRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=1000)
    location_id: Optional[int] = Field(default=None, ge=1)
