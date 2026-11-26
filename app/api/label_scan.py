from typing import Optional
from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.label_scan import (
    LabelScanAnalyzeRequest,
    LabelScanAnalyzeResponse,
    LabelScanApplyRequest,
    LabelScanApplyResponse,
    LabelScanCloseupRequest,
    LabelScanSessionCreate,
    LabelScanSessionCreateResponse,
    LabelScanSessionStatusResponse,
)
from app.schemas.system import PhotoUploadResponse
from app.services.label_scan_service import label_scan_service

router = APIRouter(prefix="/label-scan", tags=["Label Scan"])


@router.post(
    "/sessions",
    response_model=LabelScanSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_label_scan_session(
    payload: LabelScanSessionCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> LabelScanSessionCreateResponse:
    data = label_scan_service.create_session(
        db=db,
        container_id=payload.container_id,
        host_header=request.headers.get("host"),
        base_url_param=payload.base_url,
    )
    return LabelScanSessionCreateResponse(**data)


@router.get("/sessions/{token}", response_model=LabelScanSessionStatusResponse)
def get_label_scan_session(
    token: str,
    db: Session = Depends(get_db),
) -> LabelScanSessionStatusResponse:
    return label_scan_service.get_session_status(db=db, token=token)


@router.get("/sessions/{token}/qr")
def get_label_scan_qr(
    token: str,
    request: Request,
    base_url: Optional[str] = Query(default=None, max_length=300),
    db: Session = Depends(get_db),
) -> Response:
    png_bytes = label_scan_service.generate_qr_png(
        db=db,
        token=token,
        host_header=request.headers.get("host"),
        base_url_param=base_url,
    )
    return Response(content=png_bytes, media_type="image/png")


@router.post("/sessions/{token}/upload", response_model=PhotoUploadResponse)
def upload_label_scan_photo(
    token: str,
    file: UploadFile = File(...),
    zone_id: Optional[str] = Form(default=None, max_length=40),
    db: Session = Depends(get_db),
) -> PhotoUploadResponse:
    result = label_scan_service.process_upload(
        db=db,
        token=token,
        file=file,
        zone_id=zone_id,
    )
    return PhotoUploadResponse(**result)


@router.post("/sessions/{token}/analyze", response_model=LabelScanAnalyzeResponse)
async def analyze_label_scan_photo(
    token: str,
    payload: LabelScanAnalyzeRequest,
    db: Session = Depends(get_db),
) -> LabelScanAnalyzeResponse:
    return await label_scan_service.analyze_session_photo(db=db, token=token, req=payload)


@router.post("/sessions/{token}/request-closeup", response_model=LabelScanSessionStatusResponse)
def request_label_scan_closeup(
    token: str,
    payload: LabelScanCloseupRequest,
    db: Session = Depends(get_db),
) -> LabelScanSessionStatusResponse:
    return label_scan_service.request_closeup(db=db, token=token, data=payload)


@router.post("/sessions/{token}/apply", response_model=LabelScanApplyResponse)
def apply_label_scan_results(
    token: str,
    payload: LabelScanApplyRequest,
    db: Session = Depends(get_db),
) -> LabelScanApplyResponse:
    return label_scan_service.apply_labels(db=db, token=token, req=payload)
