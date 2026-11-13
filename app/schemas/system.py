from typing import List, Optional
from pydantic import BaseModel, Field


class PhotoUploadResponse(BaseModel):
    original_path: str
    thumbnail_path: str
    filename: str


class CaptureSessionCreate(BaseModel):
    label: Optional[str] = None
    base_url: Optional[str] = None
    location_id: Optional[int] = Field(default=None, ge=1)


class CaptureSessionCreateResponse(BaseModel):
    token: str
    label: str
    location_id: Optional[int] = None
    expires_in_seconds: int
    capture_url: str
    qr_url: str


class CaptureSessionStatusResponse(BaseModel):
    token: str
    status: str
    label: str
    location_id: Optional[int] = None
    photo: Optional[PhotoUploadResponse] = None
    photos: List[PhotoUploadResponse] = Field(default_factory=list)


class NetworkInfoResponse(BaseModel):
    lan_ip: str
    port: int
    base_url: str
    request_host: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    database: str
    uploads_writable: bool
    schema_version: int
    app_version: str = "1.1.0"


class BackupResponse(BaseModel):
    filename: str
    size_bytes: int
    integrity_ok: bool
    created_at: str

