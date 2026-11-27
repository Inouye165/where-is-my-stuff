from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class PhotoUploadResponse(BaseModel):
    original_path: str
    thumbnail_path: str
    filename: str


class CaptureSessionCreate(BaseModel):
    label: Optional[str] = None
    base_url: Optional[str] = None
    location_id: Optional[int] = Field(default=None, ge=1)
    high_res: bool = False


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


class BackupSummary(BaseModel):
    filename: str
    size_bytes: int
    created_at: str
    includes_uploads: bool


class BackupStatusResponse(BaseModel):
    auto_backup_enabled: bool
    interval_hours: int
    retention_count: int
    latest_backup: Optional[BackupSummary] = None
    backups: List[BackupSummary] = Field(default_factory=list)


class VisionProviderInfo(BaseModel):
    label: str
    available: bool
    model: str
    base_url: Optional[str] = None
    installed_models: List[str] = Field(default_factory=list)


class VisionProvidersResponse(BaseModel):
    default_provider: str
    providers: Dict[str, VisionProviderInfo] = Field(default_factory=dict)


class CleanupUnusedCropsResponse(BaseModel):
    dry_run: bool
    unused_crops: int


class FindLocationItemImagesResponse(BaseModel):
    status: str = "ok"
    checked_count: int
    updated_count: int

