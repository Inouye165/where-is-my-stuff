from typing import Optional
from fastapi import APIRouter, Body, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.schemas.system import (
    CaptureSessionCreate,
    CaptureSessionCreateResponse,
    CaptureSessionStatusResponse,
    PhotoUploadResponse,
    VisionProvidersResponse,
)
from app.services.capture_session_service import capture_session_service
from app.services.photo_service import photo_service
from app.services.vision_service import vision_service

router = APIRouter(prefix="/photos", tags=["Photos"])


@router.get("/vision-providers", response_model=VisionProvidersResponse)
async def get_vision_providers() -> VisionProvidersResponse:
    data = await vision_service.get_provider_status()
    return VisionProvidersResponse(**data)


@router.post("/upload", response_model=PhotoUploadResponse)
def upload_photo(
    file: UploadFile = File(...),
    max_dimension: Optional[int] = Query(default=None, ge=400, le=4096),
    jpeg_quality: int = Query(default=82, ge=60, le=98),
) -> PhotoUploadResponse:
    clamped = min(max_dimension, settings.MAX_LABEL_SCAN_DIMENSION) if max_dimension is not None else None
    result = photo_service.process_and_save(file, max_dimension=clamped, jpeg_quality=jpeg_quality)
    return PhotoUploadResponse(**result)


@router.post(
    "/capture-sessions",
    response_model=CaptureSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_capture_session(
    request: Request,
    payload: Optional[CaptureSessionCreate] = Body(default=None),
    db: Session = Depends(get_db),
) -> CaptureSessionCreateResponse:
    data = capture_session_service.create_session(
        db=db,
        label=payload.label if payload else None,
        host_header=request.headers.get("host"),
        base_url_param=payload.base_url if payload else None,
        location_id=payload.location_id if payload else None,
        high_res=bool(payload.high_res) if payload else False,
    )
    return CaptureSessionCreateResponse(**data)


@router.get("/capture-sessions/{token}", response_model=CaptureSessionStatusResponse)
def get_capture_session(token: str, db: Session = Depends(get_db)) -> CaptureSessionStatusResponse:
    sess = capture_session_service.get_session(db=db, token=token)
    photos_list = [PhotoUploadResponse(**p) for p in sess.photos]
    return CaptureSessionStatusResponse(
        token=sess.token,
        status=sess.status,
        label=sess.label,
        location_id=sess.location_id,
        photo=photos_list[-1] if photos_list else None,
        photos=photos_list,
    )


@router.get("/capture-sessions/{token}/qr")
def get_capture_session_qr(
    token: str,
    request: Request,
    base_url: Optional[str] = Query(default=None),
    hq: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> Response:
    png_bytes = capture_session_service.generate_qr_png(
        db=db,
        token=token,
        host_header=request.headers.get("host"),
        base_url_param=base_url,
        high_res=hq,
    )
    return Response(content=png_bytes, media_type="image/png")


@router.post("/capture-sessions/{token}/upload", response_model=PhotoUploadResponse)
def upload_capture_session_photo(
    token: str,
    file: UploadFile = File(...),
    max_dimension: Optional[int] = Query(default=None, ge=400, le=4096),
    db: Session = Depends(get_db),
) -> PhotoUploadResponse:
    clamped = min(max_dimension, settings.MAX_LABEL_SCAN_DIMENSION) if max_dimension is not None else None
    result = capture_session_service.process_upload(db=db, token=token, file=file, max_dimension=clamped)
    return PhotoUploadResponse(**result)
