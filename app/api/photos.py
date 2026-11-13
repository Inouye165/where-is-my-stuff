from typing import Optional
from fastapi import APIRouter, Body, File, Query, Request, Response, UploadFile, status
from app.schemas.system import (
    CaptureSessionCreate,
    CaptureSessionCreateResponse,
    CaptureSessionStatusResponse,
    PhotoUploadResponse,
)
from app.services.capture_session_service import capture_session_service
from app.services.photo_service import photo_service

router = APIRouter(prefix="/photos", tags=["Photos"])


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
    )
    return CaptureSessionCreateResponse(**data)


@router.get("/capture-sessions/{token}", response_model=CaptureSessionStatusResponse)
async def get_capture_session(token: str) -> CaptureSessionStatusResponse:
    sess = capture_session_service.get_session(token)
    return CaptureSessionStatusResponse(
        token=sess.token,
        status=sess.status,
        label=sess.label,
        photo=PhotoUploadResponse(**sess.photo) if sess.photo else None,
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
) -> PhotoUploadResponse:
    capture_session_service.require_active_session(token)
    result = photo_service.process_and_save(file)
    capture_session_service.mark_uploaded(token, result)
    return PhotoUploadResponse(**result)
