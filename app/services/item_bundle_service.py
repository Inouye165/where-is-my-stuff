import base64
import html
import ipaddress
import os
from pathlib import Path
import re
import shutil
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import anyio
from fastapi import UploadFile
import httpx
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.core.logging_config import logger
from app.models.item import ItemAttachment
from app.schemas.item import (
    BundleSavedDocument,
    BundleSavedImage,
    ItemBundleAnalysisResponse,
    ItemCreate,
    ItemDetailRead,
    ItemFromBundleCreate,
    ItemPhotoAttachmentCreate,
)
from app.services.image_lookup_service import _is_global_ip, verify_response_peer_is_public
from app.services.inventory_service import inventory_service
from app.services.photo_service import ALLOWED_EXTENSIONS, photo_service
from app.services.vision_service import _extract_json_object, _validate_model_name, vision_service

_TEXT_DOC_EXTS = {".txt", ".md", ".csv", ".json"}
_MAX_URL_BYTES = 1_000_000
_MAX_FETCH_URLS = 5
_MAX_BUNDLE_FILES = 20
_MAX_BUNDLE_PHOTO_PATHS = 20
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_BUNDLE_AI_PROMPT = (
    "You are populating ONE inventory item record from ALL user-supplied materials below: "
    "photos/screenshots, PDF datasheets/manuals/invoices, text files, web product pages, and pasted notes.\n"
    "Synthesize all sources into ONE clean JSON object with these exact keys:\n"
    "{\n"
    '  "name": "Clear, concise product/component name",\n'
    '  "part_number": "Exact model/MPN/SKU/ASIN if present, else null",\n'
    '  "category": "Short category like Sensors, Tools, Fasteners, Cables, Power, Modules",\n'
    '  "quantity": null or positive integer ONLY if an item count / pack quantity / order quantity is explicitly stated in the sources; NEVER guess or invent a count,\n'
    '  "unit": "pcs, pair, kit, pack, roll, set, etc. (default pcs)",\n'
    '  "tags": "comma-separated search keywords",\n'
    '  "specs": "Concise key electrical, mechanical, or physical specifications found in the sources",\n'
    '  "use_case": "Clear explanation of what this item is used for and common applications based on the sources",\n'
    '  "setup_info": "Practical setup, wiring, pinout, assembly, voltage/power, or usage instructions found in the sources"\n'
    "}\n"
    "STRICT RULES:\n"
    "- ONLY report part numbers, specs, quantities, wiring, and facts supported by the provided photos, PDFs, links, or text. NEVER fabricate missing specs or quantities.\n"
    "- Avoid unescaped double quotes inside string values.\n"
    "- Return ONLY valid JSON."
)


def _is_safe_public_url(raw_url: str) -> bool:
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return _is_global_ip(ip)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    saw_valid = False
    for info in infos:
        try:
            resolved_ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if not _is_global_ip(resolved_ip):
            return False
        saw_valid = True
    return saw_valid


def _extract_html_summary(raw_html: str) -> str:
    parts: List[str] = []
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()
        if title:
            parts.append(f"Title: {title}")

    meta_desc = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if meta_desc:
        desc = re.sub(r"\s+", " ", html.unescape(meta_desc.group(1))).strip()
        if desc:
            parts.append(f"Meta Description: {desc}")

    for ld_match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        ld_txt = re.sub(r"\s+", " ", ld_match.group(1)).strip()
        if ld_txt:
            parts.append(f"Structured Data: {ld_txt[:1500]}")
            break

    cleaned = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
    if cleaned:
        parts.append(cleaned[:4000])
    return "\n".join(parts)


def format_bundle_description(
    specs: Optional[str] = None,
    use_case: Optional[str] = None,
    setup_info: Optional[str] = None,
    reference_links: Optional[List[str]] = None,
) -> Optional[str]:
    sections: List[str] = []
    if specs and specs.strip():
        sections.append(f"Specs:\n{specs.strip()}")
    if use_case and use_case.strip():
        sections.append(f"Use Case:\n{use_case.strip()}")
    if setup_info and setup_info.strip():
        sections.append(f"Setup & Usage:\n{setup_info.strip()}")
    clean_links = [u.strip() for u in (reference_links or []) if u and u.strip()]
    if clean_links:
        sections.append("Links:\n" + "\n".join(clean_links))
    if not sections:
        return None
    return "\n\n".join(sections)[:20_000]


class ItemBundleService:
    async def _fetch_public_url_text(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        if not await anyio.to_thread.run_sync(_is_safe_public_url, url):
            return None, f"Skipped non-public or unreachable URL: {url}"
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=False,
                headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            ) as client:
                current = url
                raw_bytes = b""
                encoding: Optional[str] = None
                for _ in range(3):
                    async with client.stream("GET", current, follow_redirects=False) as resp:
                        if not verify_response_peer_is_public(resp):
                            return None, f"Blocked non-public peer address for URL: {url}"
                        if resp.status_code in (301, 302, 303, 307, 308):
                            loc = resp.headers.get("location")
                            if not loc:
                                return None, f"Could not fetch {url} (incomplete redirect) — link saved for reference."
                            next_url = str(httpx.URL(current).join(loc))
                            if not await anyio.to_thread.run_sync(_is_safe_public_url, next_url):
                                return None, f"Blocked unsafe redirect for URL: {url}"
                            current = next_url
                            continue
                        if resp.status_code >= 400:
                            return None, f"Could not fetch {url} (HTTP {resp.status_code}) — link saved for reference."
                        declared = str(resp.headers.get("content-length") or "")
                        if declared.isdigit() and int(declared) > _MAX_URL_BYTES * 5:
                            return None, f"Skipped oversized page ({declared} bytes): {url}"
                        chunks: List[bytes] = []
                        total = 0
                        async for chunk in resp.aiter_bytes():
                            remaining = _MAX_URL_BYTES - total
                            if remaining <= 0:
                                break
                            chunks.append(chunk[:remaining])
                            total += min(len(chunk), remaining)
                        raw_bytes = b"".join(chunks)
                        encoding = resp.encoding
                        break
                else:
                    return None, f"Too many redirects for {url} — link saved for reference."
                raw_text = raw_bytes.decode(encoding or "utf-8", errors="replace")
                summary = _extract_html_summary(raw_text)
                if not summary:
                    return None, f"Page had no readable text: {url}"
                return summary, None
        except Exception as exc:
            return None, f"Could not fetch {url} ({exc.__class__.__name__}) — link saved for reference."

    async def _invoke_bundle_ai(
        self,
        prompt: str,
        image_paths: List[Path],
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str, str]:
        selected = (provider or os.environ.get("VISION_PROVIDER") or settings.VISION_PROVIDER or "").strip().lower()
        gemini_key = vision_service._effective_gemini_key()
        openai_key = vision_service._effective_openai_key()
        if not selected or selected == "auto":
            selected = "gemini" if gemini_key else ("openai" if openai_key else "local")

        def _encode_images() -> List[Tuple[str, str]]:
            import io
            from PIL import Image, ImageOps

            encoded: List[Tuple[str, str]] = []
            max_imgs = 4 if selected == "local" else 6
            for p in image_paths[:max_imgs]:
                if not p or not p.exists():
                    continue
                try:
                    with Image.open(p) as im:
                        im = ImageOps.exif_transpose(im)
                        if im.mode not in ("RGB", "L"):
                            im = im.convert("RGB")
                        if im.width > 1280 or im.height > 1280:
                            im.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        im.save(buf, format="JPEG", quality=82, optimize=True)
                        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                        encoded.append((b64, "image/jpeg"))
                except Exception as exc:
                    logger.warning("Skipping unreadable image %s for bundle AI: %s", p.name, exc)
            return encoded

        encoded_imgs = await anyio.to_thread.run_sync(_encode_images)

        if selected == "gemini":
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY is not configured.")
            safe_model = _validate_model_name(model or settings.GEMINI_VISION_MODEL, "gemini")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{safe_model}:generateContent"
            parts: List[Dict[str, Any]] = [{"text": prompt}]
            for b64, mt in encoded_imgs:
                parts.append({"inline_data": {"mime_type": mt, "data": b64}})
            payload = {"contents": [{"parts": parts}], "generationConfig": {"response_mime_type": "application/json"}}
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers={"x-goog-api-key": gemini_key})
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini returned HTTP {resp.status_code}.")
            raw_content = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _extract_json_object(raw_content), "gemini", safe_model

        if selected == "openai":
            if not openai_key:
                raise ValueError("OPENAI_API_KEY is not configured.")
            safe_model = _validate_model_name(model or settings.OPENAI_VISION_MODEL, "openai")
            content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
            for b64, mt in encoded_imgs:
                content.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}})
            payload = {
                "model": safe_model,
                "messages": [{"role": "user", "content": content}],
                "response_format": {"type": "json_object"},
            }
            headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI returned HTTP {resp.status_code}.")
            raw_content = resp.json()["choices"][0]["message"]["content"]
            return _extract_json_object(raw_content), "openai", safe_model

        safe_model = _validate_model_name(vision_service._effective_ollama_model(model), "local")
        ollama_url = vision_service._effective_ollama_url()
        msg: Dict[str, Any] = {"role": "user", "content": prompt}
        if encoded_imgs:
            msg["images"] = [b64 for b64, _ in encoded_imgs]
        payload = {
            "model": safe_model,
            "messages": [msg],
            "format": "json",
            "stream": False,
            "options": {"num_predict": 4096, "temperature": 0.1},
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{ollama_url}/api/chat", json=payload)
            if resp.status_code == 400 and encoded_imgs:
                # Some Ollama builds/models reject multi-image or large multimodal payloads; retry text-only
                logger.warning("Local model (%s) returned HTTP 400 with %d image(s); retrying text-only.", safe_model, len(encoded_imgs))
                retry_payload = {
                    "model": safe_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "format": "json",
                    "stream": False,
                    "options": {"num_predict": 4096, "temperature": 0.1},
                }
                resp = await client.post(f"{ollama_url}/api/chat", json=retry_payload)
        if resp.status_code != 200:
            err_detail = ""
            try:
                err_detail = resp.json().get("error", "") or ""
            except Exception:
                err_detail = (resp.text or "")[:200]
            suffix = f": {err_detail}" if err_detail else ""
            raise RuntimeError(f"Local model ({safe_model}) returned HTTP {resp.status_code}{suffix}")
        resp_data = resp.json()
        raw_content = (resp_data.get("message") or {}).get("content") or resp_data.get("response") or ""
        return _extract_json_object(raw_content), "local", safe_model

    async def analyze_bundle(
        self,
        *,
        location_id: Optional[int] = None,
        files: Optional[List[UploadFile]] = None,
        photo_paths: Optional[List[str]] = None,
        urls: Optional[str] = None,
        pasted_text: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ItemBundleAnalysisResponse:
        if len(files or []) > _MAX_BUNDLE_FILES:
            raise BadRequestError(f"At most {_MAX_BUNDLE_FILES} files can be uploaded per bundle.")
        if len(photo_paths or []) > _MAX_BUNDLE_PHOTO_PATHS:
            raise BadRequestError(f"At most {_MAX_BUNDLE_PHOTO_PATHS} pre-uploaded photos can be analyzed per bundle.")

        saved_images: List[BundleSavedImage] = []
        saved_documents: List[BundleSavedDocument] = []
        ai_image_paths: List[Path] = []
        temp_dirs: List[Path] = []
        text_sections: List[str] = []
        warnings: List[str] = []

        # 1. Include pre-uploaded photo_paths (e.g. from Phone QR capture)
        seen_img_paths: set[str] = set()
        for raw_p in (photo_paths or []):
            cleaned = (raw_p or "").strip()
            if not cleaned:
                continue
            disk_p = photo_service.resolve_safe_upload_path(cleaned)
            if not disk_p.exists() or not disk_p.is_file():
                continue
            if cleaned in seen_img_paths:
                continue
            seen_img_paths.add(cleaned)
            thumb_rel = (
                f"/uploads/thumbnails/{disk_p.stem}_thumb.webp"
                if cleaned.startswith("/uploads/originals/")
                and photo_service.resolve_safe_upload_path(f"/uploads/thumbnails/{disk_p.stem}_thumb.webp").exists()
                else None
            )
            saved_images.append(BundleSavedImage(file_path=cleaned, thumbnail_path=thumb_rel, filename=disk_p.name))
            ai_image_paths.append(disk_p)

        # 2. Save uploaded files, splitting images vs PDFs/docs
        def _save_uploaded_files() -> None:
            for up in (files or []):
                if not up or not up.filename:
                    continue
                ext = Path(up.filename).suffix.lower()
                if ext in ALLOWED_EXTENSIONS:
                    res = photo_service.process_and_save(up)
                    orig = res["original_path"]
                    if orig not in seen_img_paths:
                        seen_img_paths.add(orig)
                        saved_images.append(
                            BundleSavedImage(
                                file_path=orig,
                                thumbnail_path=res.get("thumbnail_path"),
                                filename=res.get("filename") or up.filename,
                            )
                        )
                        ai_image_paths.append(photo_service.resolve_safe_upload_path(orig))
                else:
                    meta = photo_service.process_and_save_attachment(up)
                    doc_path = meta["file_path"]
                    disk_doc = photo_service.resolve_safe_upload_path(doc_path)
                    att_type = meta.get("attachment_type") or "document"
                    base_title = Path(meta["filename"]).stem.replace("_", " ").replace("-", " ").strip()[:200] or None
                    saved_documents.append(
                        BundleSavedDocument(
                            file_path=doc_path,
                            filename=meta["filename"],
                            attachment_type=att_type,
                            size_bytes=meta.get("size_bytes", 0),
                            title=base_title,
                        )
                    )
                    if att_type == "pdf":
                        try:
                            extracted_txt, extracted_imgs = vision_service._extract_pdf_text_and_images(disk_doc)
                            if extracted_txt:
                                text_sections.append(f"PDF ({meta['filename']}):\n{extracted_txt[:12000]}")
                            if extracted_imgs:
                                temp_dirs.append(extracted_imgs[0].parent)
                                ai_image_paths.extend(extracted_imgs)
                        except Exception as exc:
                            warnings.append(f"Could not read text from PDF {meta['filename']}: {exc.__class__.__name__}")
                    elif ext in _TEXT_DOC_EXTS:
                        try:
                            raw_txt = disk_doc.read_text(encoding="utf-8", errors="replace").strip()
                            if raw_txt:
                                text_sections.append(f"FILE ({meta['filename']}):\n{raw_txt[:8000]}")
                        except OSError as exc:
                            logger.debug("Could not read text attachment %s: %s", meta["filename"], exc)

        try:
            await anyio.to_thread.run_sync(_save_uploaded_files)

            # 3. Parse URLs and fetch public pages safely
            raw_url_tokens = [
                tok.strip()
                for tok in re.split(r"[\s,]+", urls or "")
                if tok and tok.strip()
            ]
            reference_links: List[str] = []
            seen_urls: set[str] = set()
            for tok in raw_url_tokens:
                if not re.match(r"^https?://", tok, flags=re.IGNORECASE):
                    continue
                if tok not in seen_urls:
                    seen_urls.add(tok)
                    reference_links.append(tok)

            for link in reference_links[:_MAX_FETCH_URLS]:
                if not await anyio.to_thread.run_sync(_is_safe_public_url, link):
                    warnings.append(f"Skipped non-public or unreachable URL: {link}")
                    continue
                page_txt, warn = await self._fetch_public_url_text(link)
                if warn:
                    warnings.append(warn)
                if page_txt:
                    text_sections.append(f"WEB LINK ({link}):\n{page_txt}")

            if pasted_text and pasted_text.strip():
                text_sections.append(f"COPIED TEXT / NOTES:\n{pasted_text.strip()[:12000]}")

            if not ai_image_paths and not text_sections:
                return ItemBundleAnalysisResponse(
                    status="fallback",
                    reference_links=reference_links,
                    saved_images=saved_images,
                    saved_documents=saved_documents,
                    warnings=warnings,
                    message="Add at least one PDF, image, link, or text note to scan.",
                )

            prompt_parts = [_BUNDLE_AI_PROMPT]
            if text_sections:
                prompt_parts.append("SOURCES:\n\n" + "\n\n---\n\n".join(text_sections))
            full_prompt = "\n\n".join(prompt_parts)

            try:
                parsed, prov_used, model_used = await self._invoke_bundle_ai(
                    full_prompt,
                    ai_image_paths,
                    provider=provider,
                    model=model,
                )
            except Exception as exc:
                logger.warning("Item bundle AI analysis failed: %s", exc)
                fallback_desc = format_bundle_description(
                    specs=None,
                    use_case=None,
                    setup_info=pasted_text.strip() if pasted_text and pasted_text.strip() else None,
                    reference_links=reference_links,
                )
                return ItemBundleAnalysisResponse(
                    status="fallback",
                    reference_links=reference_links,
                    description=fallback_desc,
                    saved_images=saved_images,
                    saved_documents=saved_documents,
                    warnings=warnings,
                    message=f"Files and links staged, but AI scan failed ({exc}). You can review and fill fields manually.",
                )

            qty_raw = parsed.get("quantity")
            qty: Optional[int] = None
            if qty_raw is not None and str(qty_raw).strip() != "":
                try:
                    parsed_qty = int(float(str(qty_raw).strip()))
                    if parsed_qty >= 0:
                        qty = min(1_000_000, parsed_qty)
                except (TypeError, ValueError):
                    qty = None

            def _clean_str(val: Any, limit: int) -> Optional[str]:
                if val is None:
                    return None
                s = str(val).strip()
                if not s or s.lower() in ("null", "none", "n/a", "unknown"):
                    return None
                return s[:limit]

            raw_tags = parsed.get("tags")
            if isinstance(raw_tags, list):
                tags_val = ", ".join(str(t).strip() for t in raw_tags if str(t).strip())[:300] or None
            else:
                tags_val = _clean_str(raw_tags, 300)

            specs = _clean_str(parsed.get("specs"), 8000)
            use_case = _clean_str(parsed.get("use_case"), 6000)
            setup_info = _clean_str(parsed.get("setup_info"), 8000)
            desc = format_bundle_description(
                specs=specs,
                use_case=use_case,
                setup_info=setup_info,
                reference_links=reference_links,
            )

            return ItemBundleAnalysisResponse(
                status="success",
                name=_clean_str(parsed.get("name"), 160),
                part_number=_clean_str(parsed.get("part_number") or parsed.get("serial_or_part_number"), 120),
                category=_clean_str(parsed.get("category"), 80),
                quantity=qty,
                unit=_clean_str(parsed.get("unit"), 30) or "pcs",
                tags=tags_val,
                specs=specs,
                use_case=use_case,
                setup_info=setup_info,
                reference_links=reference_links,
                description=desc,
                saved_images=saved_images,
                saved_documents=saved_documents,
                warnings=warnings,
                provider_used=prov_used,
                model_used=model_used,
                message=f"Autofilled item details from your sources ({model_used}).",
            )
        finally:
            for td in temp_dirs:
                shutil.rmtree(td, ignore_errors=True)

    @classmethod
    def create_item_from_bundle(cls, db: Session, data: ItemFromBundleCreate) -> ItemDetailRead:
        ordered_images: List[str] = []
        for path in ([data.main_photo_path] if data.main_photo_path else []) + list(data.image_paths or []):
            if path and path not in ordered_images:
                ordered_images.append(path)

        main_photo = data.main_photo_path or (ordered_images[0] if ordered_images else None)
        final_description = (data.description or "").strip() or format_bundle_description(
            specs=data.specs,
            use_case=data.use_case,
            setup_info=data.setup_info,
            reference_links=data.reference_links,
        )

        item = inventory_service.create(
            db,
            ItemCreate(
                name=data.name,
                description=final_description,
                category=data.category,
                tags=data.tags,
                quantity=data.quantity,
                unit=data.unit or "pcs",
                serial_or_part_number=data.serial_or_part_number,
                item_photo=main_photo,
                location_id=data.location_id,
            ),
        )

        for img_path in ordered_images:
            inventory_service.add_photo_attachment(
                db,
                item.id,
                ItemPhotoAttachmentCreate(
                    photo_path=img_path,
                    set_as_main=(img_path == main_photo),
                ),
            )

        seen_docs: set[str] = set()
        for doc_ref in (data.documents or []):
            if doc_ref.file_path in seen_docs:
                continue
            disk_doc = photo_service.resolve_safe_upload_path(doc_ref.file_path)
            if not disk_doc.exists() or not disk_doc.is_file():
                raise BadRequestError(f"Document file not found: {doc_ref.file_path}")
            seen_docs.add(doc_ref.file_path)
            ext = disk_doc.suffix.lower()
            att_type = "pdf" if ext == ".pdf" else "document"
            content_type = "application/pdf" if ext == ".pdf" else None
            filename = (doc_ref.filename or disk_doc.name).strip()[:255] or disk_doc.name
            title = (doc_ref.title or "").strip()[:200] or None
            photo_service.register_pending_reference(db, doc_ref.file_path)
            db.add(
                ItemAttachment(
                    item_id=item.id,
                    file_path=doc_ref.file_path,
                    thumbnail_path=None,
                    filename=filename,
                    content_type=content_type,
                    attachment_type=att_type,
                    title=title,
                    size_bytes=disk_doc.stat().st_size,
                )
            )

        db.flush()
        db.expire(item, ["attachments", "audit_logs"])
        return inventory_service.get_detail(db, item.id)


item_bundle_service = ItemBundleService()
