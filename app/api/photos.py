from typing import Optional
from fastapi import APIRouter, Body, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.system import (
    CaptureSessionCreate,
    CaptureSessionCreateResponse,
    CaptureSessionStatusResponse,
    PhotoUploadResponse,
)
from app.services.capture_session_service import capture_session_service
from app.services.location_service import location_service
from app.services.photo_service import photo_service
from app.services.vision_service import vision_service

router = APIRouter(prefix="/photos", tags=["Photos"])


@router.get("/vision-providers")
async def get_vision_providers() -> dict:
    return await vision_service.get_provider_status()


@router.post("/upload", response_model=PhotoUploadResponse)
async def upload_photo(file: UploadFile = File(...)) -> PhotoUploadResponse:
    result = photo_service.process_and_save(file)
    return PhotoUploadResponse(**result)


@router.post(
    "/capture-sessions",
    response_model=CaptureSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_capture_session(
    request: Request,
    payload: Optional[CaptureSessionCreate] = Body(default=None),
) -> CaptureSessionCreateResponse:
    data = capture_session_service.create_session(
        label=payload.label if payload else None,
        host_header=request.headers.get("host"),
        base_url_param=payload.base_url if payload else None,
        location_id=payload.location_id if payload else None,
    )
    return CaptureSessionCreateResponse(**data)


@router.get("/capture-sessions/{token}", response_model=CaptureSessionStatusResponse)
async def get_capture_session(token: str) -> CaptureSessionStatusResponse:
    sess = capture_session_service.get_session(token)
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
async def get_capture_session_qr(
    token: str,
    request: Request,
    base_url: Optional[str] = Query(default=None),
) -> Response:
    png_bytes = capture_session_service.generate_qr_png(
        token=token,
        host_header=request.headers.get("host"),
        base_url_param=base_url,
    )
    return Response(content=png_bytes, media_type="image/png")


@router.post("/capture-sessions/{token}/upload", response_model=PhotoUploadResponse)
async def upload_capture_session_photo(
    token: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> PhotoUploadResponse:
    sess = capture_session_service.require_active_session(token)
    result = photo_service.process_and_save(file)
    capture_session_service.mark_uploaded(token, result)
    if sess.location_id is not None:
        try:
            location_service.attach_photo(
                db=db,
                location_id=sess.location_id,
                photo_path=result["original_path"],
                thumbnail_path=result.get("thumbnail_path"),
                photo_type="container",
            )
        except Exception:
            pass
    return PhotoUploadResponse(**result)
