import io
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Optional
import uuid
import zipfile
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.exceptions import BadRequestError, NotFoundError, PayloadTooLargeError
from app.core.logging_config import logger
from app.models.item import Item
from app.models.location import Location
from app.repositories.item_repository import item_repository

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
ALLOWED_DOC_EXTENSIONS = {
    ".pdf", ".svg", ".csv", ".txt", ".md", ".json", ".xlsx", ".xls", ".doc", ".docx", ".zip"
}
READ_CHUNK_SIZE = 64 * 1024
MAX_ZIP_ENTRIES = 1000
MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class PhotoService:
    def __init__(self) -> None:
        self._cleanup_lock = threading.Lock()
        self._inflight_refs: dict[str, int] = {}
        Image.MAX_IMAGE_PIXELS = settings.MAX_IMAGE_PIXELS
        settings.init_directories()

    def register_pending_reference(self, db: Session, photo_url: Optional[str]) -> None:
        """Mark a photo as referenced by an uncommitted transaction so concurrent cleanup keeps the file."""
        if not photo_url or not photo_url.startswith("/uploads/"):
            return
        mine: set[str] = db.info.setdefault("pending_refs", set())
        if photo_url in mine:
            return
        mine.add(photo_url)
        with self._cleanup_lock:
            self._inflight_refs[photo_url] = self._inflight_refs.get(photo_url, 0) + 1

    def release_pending_references(self, db: Session) -> None:
        mine = db.info.pop("pending_refs", None)
        if not mine:
            return
        with self._cleanup_lock:
            for url in mine:
                left = self._inflight_refs.get(url, 0) - 1
                if left > 0:
                    self._inflight_refs[url] = left
                else:
                    self._inflight_refs.pop(url, None)

    def _read_upload_bounded(self, file: UploadFile) -> bytes:
        max_bytes = int(settings.MAX_IMAGE_UPLOAD_SIZE_MB) * 1024 * 1024
        chunks: list[bytes] = []
        total = 0
        file.file.seek(0)
        while True:
            chunk = file.file.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise PayloadTooLargeError(
                    f"Image upload exceeds maximum allowed size of {settings.MAX_IMAGE_UPLOAD_SIZE_MB} MB."
                )
            chunks.append(chunk)
        if total == 0:
            raise BadRequestError("Uploaded image file is empty.")
        return b"".join(chunks)

    def resolve_safe_upload_path(self, photo_url: str) -> Path:
        if not photo_url or not photo_url.startswith("/uploads/"):
            raise BadRequestError("Invalid upload path prefix.")
        if ".." in photo_url or "\\" in photo_url or "\x00" in photo_url:
            raise BadRequestError("Path traversal sequence detected in upload path.")

        rel_part = photo_url[len("/uploads/"):].lstrip("/")
        upload_root = settings.UPLOAD_DIR.resolve()
        candidate = (upload_root / rel_part).resolve()
        if not candidate.is_relative_to(upload_root):
            raise BadRequestError("Resolved path escapes upload root directory.")
        return candidate

    def process_and_save(self, file: UploadFile) -> dict[str, Any]:
        settings.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        settings.THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
        filename = file.filename or ""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise BadRequestError(
                f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        raw_bytes = self._read_upload_bounded(file)

        try:
            with Image.open(io.BytesIO(raw_bytes)) as verify_img:
                fmt = (verify_img.format or "").upper()
                if fmt not in ALLOWED_PIL_FORMATS:
                    raise BadRequestError(f"Unsupported or disguised image content format '{fmt or 'unknown'}'.")
                if verify_img.width * verify_img.height > settings.MAX_IMAGE_PIXELS:
                    raise BadRequestError("Image dimensions exceed maximum allowed pixel count.")
                verify_img.verify()
        except BadRequestError:
            raise
        except Exception as exc:
            raise BadRequestError(f"Failed to validate image content: {exc}") from exc

        unique_id = uuid.uuid4().hex
        orig_filename = f"{unique_id}{ext}"
        thumb_filename = f"{unique_id}_thumb.webp"

        orig_path = settings.ORIGINALS_DIR / orig_filename
        thumb_path = settings.THUMBNAILS_DIR / thumb_filename
        tmp_orig = settings.ORIGINALS_DIR / f".{orig_filename}.tmp"
        tmp_thumb = settings.THUMBNAILS_DIR / f".{thumb_filename}.tmp"

        pil_save_format = {
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".png": "PNG",
            ".webp": "WEBP",
            ".gif": "GIF",
            ".bmp": "BMP",
        }[ext]

        try:
            with Image.open(io.BytesIO(raw_bytes)) as opened:
                transposed = ImageOps.exif_transpose(opened)
                max_dim = settings.MAX_IMAGE_DIMENSION

                save_image = transposed.copy()
                if save_image.width > max_dim or save_image.height > max_dim:
                    save_image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                if pil_save_format == "JPEG" and save_image.mode not in ("RGB", "L"):
                    save_image = save_image.convert("RGB")

                save_kwargs: dict[str, Any] = {}
                if pil_save_format == "JPEG":
                    save_kwargs = {"quality": 82, "optimize": True}
                elif pil_save_format == "WEBP":
                    save_kwargs = {"quality": 82}

                save_image.save(tmp_orig, format=pil_save_format, **save_kwargs)

                thumb_img = transposed.copy()
                if thumb_img.mode not in ("RGB", "L"):
                    thumb_img = thumb_img.convert("RGB")
                thumb_img.thumbnail(
                    (settings.THUMBNAIL_MAX_DIMENSION, settings.THUMBNAIL_MAX_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                thumb_img.save(tmp_thumb, format="WEBP", quality=82)

            os.replace(tmp_orig, orig_path)
            os.replace(tmp_thumb, thumb_path)
        except Exception as exc:
            tmp_orig.unlink(missing_ok=True)
            tmp_thumb.unlink(missing_ok=True)
            orig_path.unlink(missing_ok=True)
            thumb_path.unlink(missing_ok=True)
            raise BadRequestError(f"Failed to process image: {exc}") from exc

        return {
            "original_path": f"/uploads/originals/{orig_filename}",
            "thumbnail_path": f"/uploads/thumbnails/{thumb_filename}",
            "filename": orig_filename,
        }

    def _crop_from_image(self, img: Image.Image, bbox: list[float], coord_mode: str = "auto") -> Optional[str]:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        try:
            coords = [float(c) for c in bbox]
        except (TypeError, ValueError):
            return None

        w, h = img.size
        if w < 16 or h < 16:
            return None

        x1, y1, x2, y2 = coords
        if coord_mode == "pixels":
            pass
        elif coord_mode == "norm_1" or (coord_mode == "auto" and max(coords) <= 1.0):
            x1, y1, x2, y2 = x1 * w, y1 * h, x2 * w, y2 * h
        elif coord_mode == "norm_1000" or (coord_mode == "auto" and max(coords) <= 1000.0):
            x1, y1, x2, y2 = (x1 / 1000.0) * w, (y1 / 1000.0) * h, (x2 / 1000.0) * w, (y2 / 1000.0) * h

        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        if (right - left) < 12 or (bottom - top) < 12:
            return None

        pad_x = max(8.0, (right - left) * 0.06)
        pad_y = max(8.0, (bottom - top) * 0.06)
        box = (
            max(0, int(round(left - pad_x))),
            max(0, int(round(top - pad_y))),
            min(w, int(round(right + pad_x))),
            min(h, int(round(bottom + pad_y))),
        )
        if box[2] - box[0] < 12 or box[3] - box[1] < 12:
            return None

        cropped = img.crop(box)
        if cropped.mode in ("RGBA", "P", "LA"):
            cropped = cropped.convert("RGB")

        settings.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        settings.THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
        unique_id = uuid.uuid4().hex
        # "_crop_" marks AI crops so they can later be replaced by a real product photo.
        orig_filename = f"ai_crop_{unique_id}.jpg"
        thumb_filename = f"ai_crop_{unique_id}_thumb.webp"
        orig_path = settings.ORIGINALS_DIR / orig_filename
        thumb_path = settings.THUMBNAILS_DIR / thumb_filename
        tmp_orig = settings.ORIGINALS_DIR / f".{orig_filename}.tmp"
        tmp_thumb = settings.THUMBNAILS_DIR / f".{thumb_filename}.tmp"

        try:
            cropped.save(tmp_orig, format="JPEG", quality=85, optimize=True)
            thumb_img = cropped.copy()
            thumb_img.thumbnail(
                (settings.THUMBNAIL_MAX_DIMENSION, settings.THUMBNAIL_MAX_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            thumb_img.save(tmp_thumb, format="WEBP", quality=82)
            os.replace(tmp_orig, orig_path)
            os.replace(tmp_thumb, thumb_path)
        except Exception:
            tmp_orig.unlink(missing_ok=True)
            tmp_thumb.unlink(missing_ok=True)
            orig_path.unlink(missing_ok=True)
            thumb_path.unlink(missing_ok=True)
            return None

        return f"/uploads/originals/{orig_filename}"

    def crop_and_save(self, source_path: Path, bbox: list[float], coord_mode: str = "auto") -> Optional[str]:
        """Crop a [x1, y1, x2, y2] region (0-1, 0-1000, or pixels) from source_path and save it."""
        if not source_path or not source_path.exists():
            return None
        try:
            with Image.open(source_path) as opened:
                img = ImageOps.exif_transpose(opened)
                return self._crop_from_image(img, bbox, coord_mode=coord_mode)
        except Exception as exc:
            logger.warning("Failed to crop bounding box from %s: %s", source_path, exc)
            return None

    def crop_many_and_save(
        self,
        source_path: Path,
        bboxes: list[Optional[list[float]]],
        coord_mode: str = "auto",
    ) -> list[Optional[str]]:
        """Open and orient source_path once, then crop all bounding boxes in memory."""
        if not source_path or not source_path.exists() or not bboxes:
            return [None for _ in bboxes]
        try:
            with Image.open(source_path) as opened:
                img = ImageOps.exif_transpose(opened)
                return [
                    self._crop_from_image(img, bbox, coord_mode=coord_mode) if bbox is not None else None
                    for bbox in bboxes
                ]
        except Exception as exc:
            logger.warning("Failed to batch-crop bounding boxes from %s: %s", source_path, exc)
            return [None for _ in bboxes]

    def delete_upload_url(self, file_url: Optional[str]) -> bool:
        if not file_url or not file_url.startswith("/uploads/"):
            return False
        try:
            resolved = self.resolve_safe_upload_path(file_url)
        except BadRequestError:
            return False

        removed = False
        if resolved.exists() and resolved.is_file():
            resolved.unlink(missing_ok=True)
            removed = True

        if file_url.startswith("/uploads/originals/"):
            thumb_candidate = (settings.THUMBNAILS_DIR / f"{resolved.stem}_thumb.webp").resolve()
            if thumb_candidate.is_relative_to(settings.UPLOAD_DIR.resolve()) and thumb_candidate.exists():
                thumb_candidate.unlink(missing_ok=True)
                removed = True
        return removed

    def cleanup_unreferenced_photo(self, db: Session, photo_url: Optional[str]) -> bool:
        if not photo_url or not photo_url.startswith("/uploads/"):
            return False

        with self._cleanup_lock:
            if self._inflight_refs.get(photo_url, 0) > 0:
                return False
            if item_repository.count_photo_references(db, photo_url) > 0:
                return False
            removed = self.delete_upload_url(photo_url)

        if removed:
            logger.info("Cleaned up unreferenced photo asset: %s", Path(photo_url).name)
        return removed

    def schedule_photo_cleanup(self, db: Session, photo_url: Optional[str]) -> None:
        if photo_url and photo_url.startswith("/uploads/"):
            db.info.setdefault("pending_photo_cleanups", set()).add(photo_url)

    def schedule_attachment_cleanup(
        self,
        db: Session,
        file_path: Optional[str],
        thumbnail_path: Optional[str] = None,
    ) -> None:
        if not file_path or not file_path.startswith("/uploads/"):
            return
        if file_path.startswith("/uploads/originals/"):
            self.schedule_photo_cleanup(db, file_path)
            return
        pending_files = db.info.setdefault("pending_file_deletes", set())
        pending_files.add(file_path)
        if thumbnail_path and thumbnail_path.startswith("/uploads/"):
            pending_files.add(thumbnail_path)

    def schedule_rollback_cleanup(self, db: Session, *file_urls: Optional[str]) -> None:
        rollback_files = db.info.setdefault("rollback_file_deletes", set())
        for url in file_urls:
            if url and url.startswith("/uploads/"):
                rollback_files.add(url)

    @staticmethod
    def _validate_document_bytes(ext: str, raw: bytes) -> None:
        if ext == ".pdf":
            if not raw.startswith(b"%PDF-"):
                raise BadRequestError("Uploaded file is not a valid PDF document.")
            return

        if ext in {".zip", ".docx", ".xlsx"}:
            if not any(raw.startswith(sig) for sig in _ZIP_MAGICS):
                raise BadRequestError(f"Uploaded file is not a valid {ext.upper().lstrip('.')} archive.")
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    infos = zf.infolist()
                    if len(infos) > MAX_ZIP_ENTRIES:
                        raise BadRequestError("Archive contains too many entries.")
                    if sum(info.file_size for info in infos) > MAX_ZIP_UNCOMPRESSED_BYTES:
                        raise BadRequestError("Archive uncompressed size exceeds allowed limit.")
            except BadRequestError:
                raise
            except (zipfile.BadZipFile, OSError) as exc:
                raise BadRequestError(f"Corrupted or invalid {ext.upper().lstrip('.')} archive.") from exc
            return

        if ext in {".doc", ".xls"}:
            if not raw.startswith(_OLE2_MAGIC):
                raise BadRequestError(f"Uploaded file is not a valid {ext.upper().lstrip('.')} document.")
            return

        if ext in {".json", ".svg", ".txt", ".md", ".csv"}:
            if b"\x00" in raw:
                raise BadRequestError("Text attachment contains invalid binary null bytes.")
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BadRequestError("Text attachment must be valid UTF-8.") from exc
            if ext == ".json":
                try:
                    json.loads(decoded)
                except json.JSONDecodeError as exc:
                    raise BadRequestError("Uploaded JSON file is not valid JSON.") from exc
            elif ext == ".svg":
                if "<svg" not in decoded.lower():
                    raise BadRequestError("Uploaded SVG file does not contain an <svg> element.")

    def process_and_save_attachment(self, file: UploadFile) -> dict[str, Any]:
        if not file or not file.filename:
            raise BadRequestError("No file provided.")

        original_name = Path(file.filename).name
        ext = Path(original_name).suffix.lower()
        if ext in ALLOWED_EXTENSIONS:
            saved = self.process_and_save(file)
            saved_disk_path = settings.ORIGINALS_DIR / saved["filename"]
            size_bytes = saved_disk_path.stat().st_size if saved_disk_path.exists() else 0
            return {
                "file_path": saved["original_path"],
                "thumbnail_path": saved["thumbnail_path"],
                "filename": original_name[:255],
                "content_type": file.content_type or "image/jpeg",
                "attachment_type": "image",
                "size_bytes": size_bytes,
            }

        if ext not in ALLOWED_DOC_EXTENSIONS:
            raise BadRequestError(
                f"Unsupported file type '{ext}'. Allowed: images, PDF, SVG, CSV, TXT, MD, JSON, Office docs, ZIP."
            )

        raw = self._read_upload_bounded(file)
        if not raw:
            raise BadRequestError("Uploaded file is empty.")
        self._validate_document_bytes(ext, raw)

        settings.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        file_id = uuid.uuid4().hex
        stored_name = f"{file_id}{ext}"
        target_path = (settings.ATTACHMENTS_DIR / stored_name).resolve()
        tmp_path = (settings.ATTACHMENTS_DIR / f".{stored_name}.tmp").resolve()
        if not target_path.is_relative_to(settings.ATTACHMENTS_DIR.resolve()):
            raise BadRequestError("Invalid attachment filename.")

        try:
            tmp_path.write_bytes(raw)
            os.replace(tmp_path, target_path)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            target_path.unlink(missing_ok=True)
            raise BadRequestError(f"Failed to save attachment: {exc}") from exc
        attachment_type = "pdf" if ext == ".pdf" else ("chart" if ext in {".svg", ".csv"} else "document")
        return {
            "file_path": f"/uploads/attachments/{stored_name}",
            "thumbnail_path": None,
            "filename": original_name[:255],
            "content_type": file.content_type or "application/octet-stream",
            "attachment_type": attachment_type,
            "size_bytes": len(raw),
        }

    def convert_photos_to_pdf_attachment(
        self,
        photo_paths: list[str],
        filename: str,
        max_dimension: int = 1400,
    ) -> dict[str, Any]:
        if not photo_paths:
            raise BadRequestError("Select at least one photo to convert to PDF.")

        pages: list[Image.Image] = []
        try:
            for photo_url in photo_paths:
                disk_path = self.resolve_safe_upload_path(photo_url)
                if not disk_path.exists() or not disk_path.is_file():
                    raise NotFoundError(f"Photo not found: {photo_url}")
                try:
                    with Image.open(disk_path) as src_im:
                        transposed = ImageOps.exif_transpose(src_im)
                        rgb = transposed.convert("RGB")
                        if rgb.width > max_dimension or rgb.height > max_dimension:
                            rgb.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                        pages.append(rgb)
                except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
                    raise BadRequestError(f"Could not read image for PDF conversion: {Path(photo_url).name}") from exc

            settings.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
            file_id = uuid.uuid4().hex
            stored_name = f"{file_id}.pdf"
            target_path = (settings.ATTACHMENTS_DIR / stored_name).resolve()
            tmp_path = (settings.ATTACHMENTS_DIR / f".{stored_name}.tmp").resolve()
            if not target_path.is_relative_to(settings.ATTACHMENTS_DIR.resolve()):
                raise BadRequestError("Invalid attachment filename.")

            try:
                pages[0].save(
                    tmp_path,
                    format="PDF",
                    resolution=150.0,
                    save_all=True,
                    append_images=pages[1:],
                    quality=75,
                    optimize=True,
                )
                os.replace(tmp_path, target_path)
            except Exception as exc:
                tmp_path.unlink(missing_ok=True)
                target_path.unlink(missing_ok=True)
                raise BadRequestError(f"Failed to generate PDF: {exc}") from exc
        finally:
            for im in pages:
                try:
                    im.close()
                except Exception:
                    pass

        safe_filename = re.sub(r"[^A-Za-z0-9._\- ]+", "_", (filename or "Document").strip()).strip(" ._") or "Document"
        if not safe_filename.lower().endswith(".pdf"):
            safe_filename = f"{safe_filename[:240]}.pdf"
        size_bytes = target_path.stat().st_size if target_path.exists() else 0
        return {
            "file_path": f"/uploads/attachments/{stored_name}",
            "thumbnail_path": None,
            "filename": safe_filename[:255],
            "content_type": "application/pdf",
            "attachment_type": "pdf",
            "size_bytes": size_bytes,
        }

    def sweep_orphan_ai_crops(self, db: Session, max_age_seconds: float = 24 * 3600, dry_run: bool = False) -> int:
        """Count (dry_run) or delete old AI-detection crops that nothing references. Manual use only.

        Refuses to run against a database with no items or locations, since every file would look orphaned.
        """
        if db.execute(select(func.count(Item.id))).scalar() == 0 and db.execute(
            select(func.count(Location.id))
        ).scalar() == 0:
            return 0
        removed = 0
        cutoff = time.time() - max_age_seconds
        for path in settings.ORIGINALS_DIR.glob("ai_crop_*"):
            try:
                if not path.is_file() or path.name.startswith(".") or path.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            url = f"/uploads/originals/{path.name}"
            if dry_run:
                with self._cleanup_lock:
                    if self._inflight_refs.get(url, 0) == 0 and item_repository.count_photo_references(db, url) == 0:
                        removed += 1
                continue
            if self.cleanup_unreferenced_photo(db, url):
                removed += 1
        return removed

    def delete_attachment_files(self, db: Session, file_path: Optional[str], thumbnail_path: Optional[str] = None) -> None:
        if not file_path or not file_path.startswith("/uploads/"):
            return
        if file_path.startswith("/uploads/originals/"):
            self.cleanup_unreferenced_photo(db, file_path)
            return
        self.delete_upload_url(file_path)
        if thumbnail_path:
            self.delete_upload_url(thumbnail_path)


photo_service = PhotoService()


@event.listens_for(Session, "after_commit")
def _run_deferred_session_file_cleanups(session: Session) -> None:
    photo_service.release_pending_references(session)
    session.info.pop("rollback_file_deletes", None)
    direct_files = list(session.info.pop("pending_file_deletes", ()))
    photo_urls = list(session.info.pop("pending_photo_cleanups", ()))

    for file_url in direct_files:
        photo_service.delete_upload_url(file_url)

    if photo_urls:
        bind = session.get_bind()
        with Session(bind=bind) as check_db:
            for photo_url in photo_urls:
                photo_service.cleanup_unreferenced_photo(check_db, photo_url)


@event.listens_for(Session, "after_rollback")
def _run_rollback_session_file_cleanups(session: Session) -> None:
    photo_service.release_pending_references(session)
    session.info.pop("pending_file_deletes", None)
    session.info.pop("pending_photo_cleanups", None)
    rollback_files = list(session.info.pop("rollback_file_deletes", ()))
    for file_url in rollback_files:
        photo_service.delete_upload_url(file_url)
