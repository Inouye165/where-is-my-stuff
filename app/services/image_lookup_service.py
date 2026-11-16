from io import BytesIO
import ipaddress
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from fastapi import UploadFile
import anyio
import httpx

from app.core.exceptions import AppError
from app.services.photo_service import photo_service

logger = logging.getLogger("wims.image_lookup")

_COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
_WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
_OPENVERSE_API_URL = "https://api.openverse.org/v1/images/"
_USER_AGENT = "WhereIsMyStuffInventory/1.1 (https://localhost; admin@localhost.localdomain)"
_ALLOWED_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def is_crop_or_missing_photo(item_photo: Optional[str]) -> bool:
    if not item_photo:
        return True
    return "_crop_" in str(item_photo)


def _is_safe_public_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass
    return True


def build_search_queries(
    name: str,
    part_number: Optional[str] = None,
    category: Optional[str] = None,
) -> List[str]:
    queries: List[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        cleaned = re.sub(r"\s+", " ", q).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            queries.append(cleaned)

    clean_part = (part_number or "").strip()
    clean_name = (name or "").strip()
    clean_cat = (category or "").strip()

    if clean_part and clean_cat:
        _add(f"{clean_part} {clean_cat}")
    if clean_part:
        _add(clean_part)
    if clean_name and clean_cat and clean_cat.lower() not in clean_name.lower():
        _add(f"{clean_name} {clean_cat}")
    if clean_name:
        _add(clean_name)

    # Electronics part-family fallbacks (e.g. L7824 / LM7805 / LM317 -> 78xx / TO-220 voltage regulator)
    combined = f"{clean_part} {clean_name} {clean_cat}"
    if re.search(r"\bL?M?78\d{2}[A-Z]?\b|\bL?M?79\d{2}[A-Z]?\b", combined, re.IGNORECASE):
        _add("78xx voltage regulator TO-220")
    if re.search(r"\bLM317[A-Z]?\b", combined, re.IGNORECASE):
        _add("LM317 voltage regulator TO-220")
    if clean_cat:
        _add(clean_cat)

    return queries


async def _search_wikimedia_commons(client: httpx.AsyncClient, query: str) -> Optional[str]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrnamespace": "6",
        "gsrsearch": query,
        "gsrlimit": "6",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "iiurlwidth": "640",
        "format": "json",
    }
    resp = await client.get(_COMMONS_API_URL, params=params)
    if resp.status_code != 200:
        return None
    pages = (resp.json().get("query") or {}).get("pages") or {}
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime") or "").lower()
        if mime not in _ALLOWED_MIME_TYPES:
            continue
        candidate = info.get("thumburl") or info.get("url")
        if candidate and _is_safe_public_https_url(candidate):
            return str(candidate)
    return None


async def _search_wikipedia_pageimage(client: httpx.AsyncClient, query: str) -> Optional[str]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "5",
        "prop": "pageimages",
        "piprop": "thumbnail|original",
        "pithumbsize": "640",
        "format": "json",
    }
    resp = await client.get(_WIKIPEDIA_API_URL, params=params)
    if resp.status_code != 200:
        return None
    pages = (resp.json().get("query") or {}).get("pages") or {}
    for page in pages.values():
        thumb = (page.get("thumbnail") or {}).get("source")
        orig = (page.get("original") or {}).get("source")
        candidate = thumb or orig
        if candidate and _is_safe_public_https_url(candidate) and not str(candidate).lower().endswith(".svg"):
            return str(candidate)
    return None


async def _search_openverse(client: httpx.AsyncClient, query: str) -> Optional[str]:
    resp = await client.get(_OPENVERSE_API_URL, params={"q": query, "page_size": "5"})
    if resp.status_code != 200:
        return None
    for item in resp.json().get("results") or []:
        candidate = item.get("url") or item.get("thumbnail")
        if candidate and _is_safe_public_https_url(candidate) and not str(candidate).lower().endswith(".svg"):
            return str(candidate)
    return None


async def find_image_url(
    client: httpx.AsyncClient,
    name: str,
    part_number: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[str]:
    queries = build_search_queries(name=name, part_number=part_number, category=category)
    for query in queries:
        try:
            url = await _search_wikimedia_commons(client, query)
            if url:
                return url
            url = await _search_wikipedia_pageimage(client, query)
            if url:
                return url
            url = await _search_openverse(client, query)
            if url:
                return url
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Image search request failed for %r: %s", query, exc)
            continue
    return None


async def download_and_save_image(client: httpx.AsyncClient, url: str) -> Optional[str]:
    if not _is_safe_public_https_url(url):
        return None
    # Follow redirects manually so every hop gets the same public-host check.
    resp: Optional[httpx.Response] = None
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            resp = await client.get(current, follow_redirects=False)
        except httpx.HTTPError as exc:
            logger.debug("Image download failed for %s: %s", current, exc)
            return None
        if resp.status_code not in _REDIRECT_STATUSES:
            break
        location = str(resp.headers.get("location") or "")
        current = urljoin(current, location)
        if not location or not _is_safe_public_https_url(current):
            logger.debug("Refusing image redirect from %s to %r", url, location)
            return None
    else:
        return None
    if resp is None or resp.status_code != 200 or not resp.content or len(resp.content) > _MAX_IMAGE_BYTES:
        return None

    content_type = str(resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    final_path = urlparse(current).path.lower()
    ext = ".jpg"
    if content_type == "image/png" or final_path.endswith(".png"):
        ext = ".png"
    elif content_type == "image/webp" or final_path.endswith(".webp"):
        ext = ".webp"

    upload = UploadFile(filename=f"auto_import{ext}", file=BytesIO(resp.content))
    try:
        saved = await anyio.to_thread.run_sync(photo_service.process_and_save, upload)
    except AppError as exc:
        logger.debug("Downloaded image from %s rejected: %s", url, exc.detail)
        return None
    return saved["original_path"]


async def find_and_import_image(
    name: str,
    part_number: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[str]:
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
        url = await find_image_url(client, name=name, part_number=part_number, category=category)
        if not url:
            return None
        return await download_and_save_image(client, url)


async def find_and_import_images_batch(
    items: List[dict],
) -> Dict[int, str]:
    """Resolve and save photos for multiple items, caching identical search URLs in-memory."""
    results: Dict[int, str] = {}
    url_to_saved: Dict[str, str] = {}
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
        for item in items:
            item_id = int(item["id"])
            url = await find_image_url(
                client,
                name=item.get("name") or "",
                part_number=item.get("serial_or_part_number"),
                category=item.get("category"),
            )
            if not url:
                continue
            if url in url_to_saved:
                results[item_id] = url_to_saved[url]
                continue
            saved_path = await download_and_save_image(client, url)
            if saved_path:
                url_to_saved[url] = saved_path
                results[item_id] = saved_path
    return results
