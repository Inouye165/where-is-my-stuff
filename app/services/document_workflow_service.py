from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
from typing import Optional
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.audit import AuditLog
from app.models.item import ItemAttachment
from app.repositories.item_repository import item_repository
from app.schemas.document import (
    AttachmentTypesetRequest,
    DocumentEnhanceOptions,
    DocumentSaveRequest,
    DocumentSaveResponse,
)
from app.schemas.item import ItemAttachmentRead
from app.services.document_enhance_service import document_enhance_service
from app.services.document_pdf_service import document_pdf_service
from app.services.photo_service import photo_service


class DocumentWorkflowService:
    """Coordinates multi-page document enhancement and dual-PDF attachment persistence."""

    @staticmethod
    def _resolve_existing_upload(photo_url: str) -> Path:
        disk_path = photo_service.resolve_safe_upload_path(photo_url)
        if not disk_path.exists() or not disk_path.is_file():
            raise NotFoundError(f"Page image not found: {photo_url}")
        return disk_path

    def save_document_to_item(
        self,
        db: Session,
        item_id: int,
        data: DocumentSaveRequest,
    ) -> DocumentSaveResponse:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")

        clean_title = (data.title or "").strip()[:160] or f"{item.name} Document"[:160]
        page_paths: list[Path] = []
        warnings: list[str] = []

        for page in data.pages:
            if page.enhanced_path:
                page_paths.append(self._resolve_existing_upload(page.enhanced_path))
            elif data.auto_enhance_unprocessed:
                enhanced = document_enhance_service.enhance_photo_file(
                    page.source_path,
                    options=page.enhance_options or DocumentEnhanceOptions(),
                )
                warnings.extend(enhanced.get("warnings") or [])
                page_paths.append(self._resolve_existing_upload(enhanced["enhanced_path"]))
            else:
                page_paths.append(self._resolve_existing_upload(page.source_path))

        enhanced_att: Optional[ItemAttachment] = None
        typeset_att: Optional[ItemAttachment] = None
        ocr_words = 0
        ocr_available = True

        if data.save_enhanced_pdf:
            scan_bytes = document_pdf_service.build_enhanced_scan_pdf(page_paths, pdf_dpi=data.pdf_dpi)
            meta = photo_service.save_attachment_bytes(
                scan_bytes,
                filename=f"{clean_title}.pdf",
                content_type="application/pdf",
                attachment_type="pdf",
                ext=".pdf",
            )
            photo_service.schedule_rollback_cleanup(db, meta["file_path"])
            enhanced_att = ItemAttachment(
                item_id=item.id,
                file_path=meta["file_path"],
                thumbnail_path=None,
                filename=meta["filename"],
                content_type=meta["content_type"],
                attachment_type="pdf",
                title=clean_title,
                size_bytes=meta["size_bytes"],
            )
            db.add(enhanced_att)

        if data.save_typeset_pdf:
            typeset_bytes, ocr_words, ocr_available, pdf_warnings = document_pdf_service.build_typeset_pdf(
                page_paths,
                mode=data.typeset_mode,
                include_graphics=data.include_graphics,
            )
            warnings.extend(pdf_warnings)
            typeset_title = f"{clean_title} (Typeset)"[:160] if data.save_enhanced_pdf else clean_title
            meta = photo_service.save_attachment_bytes(
                typeset_bytes,
                filename=f"{typeset_title}.pdf",
                content_type="application/pdf",
                attachment_type="pdf",
                ext=".pdf",
            )
            photo_service.schedule_rollback_cleanup(db, meta["file_path"])
            typeset_att = ItemAttachment(
                item_id=item.id,
                file_path=meta["file_path"],
                thumbnail_path=None,
                filename=meta["filename"],
                content_type=meta["content_type"],
                attachment_type="pdf",
                title=typeset_title,
                size_bytes=meta["size_bytes"],
            )
            db.add(typeset_att)

        now = datetime.now(timezone.utc)
        item.updated_at = now
        saved_labels = [lbl for lbl, att in (("Enhanced Scan PDF", enhanced_att), ("Typeset PDF", typeset_att)) if att]
        db.add(
            AuditLog(
                item_id=item.id,
                location_id=item.location_id,
                action="document_added",
                notes=f"Added document '{clean_title}' ({len(page_paths)} page(s): {', '.join(saved_labels)})",
                recorded_at=now,
            )
        )
        db.flush()
        if enhanced_att is not None:
            db.refresh(enhanced_att)
        if typeset_att is not None:
            db.refresh(typeset_att)

        return DocumentSaveResponse(
            enhanced_pdf=ItemAttachmentRead.model_validate(enhanced_att) if enhanced_att else None,
            typeset_pdf=ItemAttachmentRead.model_validate(typeset_att) if typeset_att else None,
            page_count=len(page_paths),
            ocr_word_count=ocr_words,
            ocr_available=ocr_available,
            warnings=warnings,
        )

    def create_typeset_from_attachment(
        self,
        db: Session,
        item_id: int,
        attachment_id: int,
        data: AttachmentTypesetRequest,
    ) -> DocumentSaveResponse:
        item = item_repository.get_by_id(db, item_id)
        if not item:
            raise NotFoundError("Item not found.")

        att = db.execute(
            select(ItemAttachment).where(
                ItemAttachment.id == attachment_id,
                ItemAttachment.item_id == item_id,
            )
        ).scalar_one_or_none()
        if not att:
            raise NotFoundError("Attachment not found.")

        disk_path = self._resolve_existing_upload(att.file_path)
        base_title = (data.title or att.title or Path(att.filename).stem or item.name).strip()[:145]
        clean_title = f"{base_title} (Typeset)"[:160]
        warnings: list[str] = []

        if att.attachment_type == "image":
            if data.enhance_first:
                enhanced = document_enhance_service.enhance_photo_file(att.file_path, DocumentEnhanceOptions())
                page_paths = [self._resolve_existing_upload(enhanced["enhanced_path"])]
                warnings.extend(enhanced.get("warnings") or [])
            else:
                page_paths = [disk_path]

            typeset_bytes, ocr_words, ocr_available, pdf_warnings = document_pdf_service.build_typeset_pdf(
                page_paths,
                mode=data.typeset_mode,
                include_graphics=data.include_graphics,
            )
            warnings.extend(pdf_warnings)
            page_count = 1
        elif att.attachment_type == "pdf" or disk_path.suffix.lower() == ".pdf":
            reader = PdfReader(str(disk_path))
            with tempfile.TemporaryDirectory(prefix="wims_pdf_extract_") as tmp_dir:
                extracted_paths: list[Path] = []
                temp_upload_urls: list[str] = []
                try:
                    for p_idx, page in enumerate(reader.pages, start=1):
                        images = list(getattr(page, "images", []) or [])
                        if not images:
                            continue
                        best_img = max(images, key=lambda im: len(im.data or b""))
                        out_p = Path(tmp_dir) / f"page_{p_idx}.png"
                        with Image.open(io.BytesIO(best_img.data)) as pil_im:
                            pil_im.convert("RGB").save(out_p, format="PNG")
                        extracted_paths.append(out_p)

                    if not extracted_paths:
                        raise BadRequestError(
                            "This PDF does not contain embedded page images to typeset (it may already be pure vector text)."
                        )

                    if data.enhance_first:
                        final_paths: list[Path] = []
                        for ep in extracted_paths:
                            with Image.open(ep) as pil_ep:
                                saved_tmp = photo_service.save_pil_image(
                                    pil_ep, ext=".jpg", max_dimension=3200, prefix="doc_"
                                )
                            temp_upload_urls.append(saved_tmp["original_path"])
                            enh = document_enhance_service.enhance_photo_file(
                                saved_tmp["original_path"],
                                DocumentEnhanceOptions(auto_perspective=False),
                            )
                            temp_upload_urls.append(enh["enhanced_path"])
                            final_paths.append(self._resolve_existing_upload(enh["enhanced_path"]))
                    else:
                        final_paths = extracted_paths

                    typeset_bytes, ocr_words, ocr_available, pdf_warnings = document_pdf_service.build_typeset_pdf(
                        final_paths,
                        mode=data.typeset_mode,
                        include_graphics=data.include_graphics,
                    )
                    warnings.extend(pdf_warnings)
                    page_count = len(final_paths)
                finally:
                    for url in temp_upload_urls:
                        photo_service.delete_upload_url(url)
        else:
            raise BadRequestError("Only image or scanned PDF attachments can be converted into a Typeset PDF.")

        meta = photo_service.save_attachment_bytes(
            typeset_bytes,
            filename=f"{clean_title}.pdf",
            content_type="application/pdf",
            attachment_type="pdf",
            ext=".pdf",
        )
        photo_service.schedule_rollback_cleanup(db, meta["file_path"])
        typeset_att = ItemAttachment(
            item_id=item.id,
            file_path=meta["file_path"],
            thumbnail_path=None,
            filename=meta["filename"],
            content_type=meta["content_type"],
            attachment_type="pdf",
            title=clean_title,
            size_bytes=meta["size_bytes"],
        )
        db.add(typeset_att)
        now = datetime.now(timezone.utc)
        item.updated_at = now
        db.add(
            AuditLog(
                item_id=item.id,
                location_id=item.location_id,
                action="document_added",
                notes=f"Created Typeset PDF '{clean_title}' from attachment #{att.id}",
                recorded_at=now,
            )
        )
        db.flush()
        db.refresh(typeset_att)

        return DocumentSaveResponse(
            enhanced_pdf=None,
            typeset_pdf=ItemAttachmentRead.model_validate(typeset_att),
            page_count=page_count,
            ocr_word_count=ocr_words,
            ocr_available=ocr_available,
            warnings=warnings,
        )


document_workflow_service = DocumentWorkflowService()
