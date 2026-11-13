import io
import os
from pathlib import Path
from typing import Any, Optional
import uuid
from fastapi import UploadFile
from PIL import Image, ImageOps
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.exceptions import BadRequestError, PayloadTooLargeError
from app.core.logging_config import logger
from app.repositories.item_repository import item_repository

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
READ_CHUNK_SIZE = 64 * 1024


class PhotoService:
    def __init__(self) -> None:
        settings.init_directories()

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
        settings.init_directories()
        filename = file.filename or ""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise BadRequestError(
                f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        raw_bytes = self._read_upload_bounded(file)
        Image.MAX_IMAGE_PIXELS = settings.MAX_IMAGE_PIXELS

        try:
            with Image.open(io.BytesIO(raw_bytes)) as verify_img:
                fmt = (verify_img.format or "").upper()
                if fmt not in ALLOWED_PIL_FORMATS:
                    raise BadRequestError(f"Unsupported or disguised image content format '{fmt or 'unknown'}'.")
                verify_img.verify()
        except AppImageReject:
            raise
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

                if pil_save_format == "JPEG" and save_image.mode in ("RGBA", "P", "LA"):
                    save_image = save_image.convert("RGB")

                save_kwargs: dict[str, Any] = {}
                if pil_save_format == "JPEG":
                    save_kwargs = {"quality": 82, "optimize": True}
                elif pil_save_format == "WEBP":
                    save_kwargs = {"quality": 82}

                save_image.save(tmp_orig, format=pil_save_format, **save_kwargs)

                thumb_img = transposed.copy()
                if thumb_img.mode in ("RGBA", "P", "LA"):
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

    def cleanup_unreferenced_photo(self, db: Session, photo_url: Optional[str]) -> bool:
        if not photo_url or not photo_url.startswith("/uploads/"):
            return False

        if item_repository.count_photo_references(db, photo_url) > 0:
            return False

        try:
            orig_file = self.resolve_safe_upload_path(photo_url)
        except BadRequestError:
            return False

        stem = orig_file.stem
        thumb_candidate = (settings.THUMBNAILS_DIR / f"{stem}_thumb.webp").resolve()
        removed = False

        if orig_file.exists() and orig_file.is_file():
            orig_file.unlink(missing_ok=True)
            removed = True

        if thumb_candidate.is_relative_to(settings.UPLOAD_DIR.resolve()) and thumb_candidate.exists():
            thumb_candidate.unlink(missing_ok=True)
            removed = True

        if removed:
            logger.info("Cleaned up unreferenced photo asset: %s", orig_file.name)
        return removed


class AppImageReject(Exception):
    pass


photo_service = PhotoService()
