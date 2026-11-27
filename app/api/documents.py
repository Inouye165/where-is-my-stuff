import anyio
from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.schemas.document import (
    AttachmentRotateRequest,
    AttachmentTypesetRequest,
    DocumentDetectCornersRequest,
    DocumentDetectCornersResponse,
    DocumentEnhanceRequest,
    DocumentEnhanceResponse,
    DocumentSaveRequest,
    DocumentSaveResponse,
    DocumentStitchPatchRequest,
    DocumentStitchPatchResponse,
    DocumentTypesetPreviewRequest,
    ScannerListResponse,
    ScannerScanRequest,
    ScannerScanResponse,
)
from app.schemas.item import ItemAttachmentRead
from app.services.document_enhance_service import document_enhance_service
from app.services.document_pdf_service import document_pdf_service
from app.services.document_stitch_service import document_stitch_service
from app.services.document_workflow_service import document_workflow_service
from app.services.photo_service import photo_service
from app.services.scanner_service import scanner_service

router = APIRouter(tags=["Documents"])


@router.get("/documents/scanners", response_model=ScannerListResponse)
async def list_scanners() -> ScannerListResponse:
    data = await anyio.to_thread.run_sync(scanner_service.list_scanners)
    return ScannerListResponse(**data)


@router.post("/documents/scan", response_model=ScannerScanResponse)
async def scan_document_page(
    payload: ScannerScanRequest = Body(default_factory=ScannerScanRequest),
) -> ScannerScanResponse:
    data = await anyio.to_thread.run_sync(
        lambda: scanner_service.scan_page(
            device_id=payload.device_id,
            dpi=payload.dpi,
            color_mode=payload.color_mode,
            source=payload.source,
        )
    )
    return ScannerScanResponse(**data)


@router.post("/documents/detect-corners", response_model=DocumentDetectCornersResponse)
async def detect_document_corners(payload: DocumentDetectCornersRequest) -> DocumentDetectCornersResponse:
    data = await anyio.to_thread.run_sync(document_enhance_service.detect_corners, payload.photo_path)
    return DocumentDetectCornersResponse(**data)


@router.post("/documents/enhance", response_model=DocumentEnhanceResponse)
async def enhance_document_page(payload: DocumentEnhanceRequest) -> DocumentEnhanceResponse:
    data = await anyio.to_thread.run_sync(
        document_enhance_service.enhance_photo_file,
        payload.photo_path,
        payload.options,
    )
    return DocumentEnhanceResponse(**data)


@router.post("/documents/preview-typeset")
async def preview_typeset_pdf(payload: DocumentTypesetPreviewRequest) -> Response:
    disk_path = photo_service.resolve_safe_upload_path(payload.photo_path)
    if not disk_path.exists() or not disk_path.is_file():
        raise NotFoundError(f"Photo not found: {payload.photo_path}")
    pdf_bytes, words, ocr_ok, _warnings = await anyio.to_thread.run_sync(
        lambda: document_pdf_service.build_typeset_pdf(
            [disk_path],
            mode=payload.typeset_mode,
            include_graphics=payload.include_graphics,
        )
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Wims-Ocr-Words": str(words),
            "X-Wims-Ocr-Available": "1" if ocr_ok else "0",
        },
    )


@router.post("/documents/stitch-patch", response_model=DocumentStitchPatchResponse)
async def stitch_document_closeup_patch(payload: DocumentStitchPatchRequest) -> DocumentStitchPatchResponse:
    data = await anyio.to_thread.run_sync(
        lambda: document_stitch_service.stitch_closeup_patch(
            base_photo_path=payload.base_photo_path,
            patch_photo_path=payload.patch_photo_path,
            enhance_patch=payload.enhance_patch,
            max_dimension=payload.max_dimension,
        )
    )
    return DocumentStitchPatchResponse(**data)


@router.post(
    "/items/{item_id}/documents",
    response_model=DocumentSaveResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_item_document(
    item_id: int,
    payload: DocumentSaveRequest,
    db: Session = Depends(get_db),
) -> DocumentSaveResponse:
    return document_workflow_service.save_document_to_item(db=db, item_id=item_id, data=payload)


@router.post(
    "/items/{item_id}/attachments/{attachment_id}/typeset",
    response_model=DocumentSaveResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_typeset_from_attachment(
    item_id: int,
    attachment_id: int,
    payload: AttachmentTypesetRequest = Body(default_factory=AttachmentTypesetRequest),
    db: Session = Depends(get_db),
) -> DocumentSaveResponse:
    return document_workflow_service.create_typeset_from_attachment(
        db=db,
        item_id=item_id,
        attachment_id=attachment_id,
        data=payload,
    )


@router.get("/items/{item_id}/attachments/{attachment_id}/preview")
def preview_rotated_item_attachment(
    item_id: int,
    attachment_id: int,
    rotation_deg: int = Query(default=0),
    db: Session = Depends(get_db),
) -> Response:
    pdf_bytes = document_workflow_service.render_rotated_pdf_preview(
        db=db,
        item_id=item_id,
        attachment_id=attachment_id,
        rotation_deg=rotation_deg,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "frame-ancestors 'self'",
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/items/{item_id}/attachments/{attachment_id}/rotate",
    response_model=ItemAttachmentRead,
)
def rotate_item_attachment(
    item_id: int,
    attachment_id: int,
    payload: AttachmentRotateRequest,
    db: Session = Depends(get_db),
) -> ItemAttachmentRead:
    return document_workflow_service.rotate_attachment(
        db=db,
        item_id=item_id,
        attachment_id=attachment_id,
        rotation_deg=payload.rotation_deg,
    )
