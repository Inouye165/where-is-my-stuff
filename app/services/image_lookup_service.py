import asyncio
from io import BytesIO
import ipaddress
import logging
import re
import socket
from typing import Any, Dict, List, Mapping, Optional, Union
from urllib.parse import urljoin, urlparse

from fastapi import UploadFile
import anyio
import httpx

from app.core.database import session_scope
from app.core.exceptions import AppError, NotFoundError
from app.schemas.item import FindItemImageResponse, ItemRead, ItemUpdate
from app.schemas.system import FindLocationItemImagesResponse
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
_MAX_BATCH_ITEMS = 50
_BATCH_CONCURRENCY = 4


def is_crop_or_missing_photo(item_photo: Optional[str]) -> bool:
    if not item_photo:
        return True
    return "_crop_" in str(item_photo)


def _is_global_ip(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_reserved
        and not ip.is_multicast
        and not ip.is_unspecified
    )


def _is_safe_public_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if not _is_global_ip(ip):
            return False
    except ValueError:
        pass
    return True


async def _reject_oversized_response(response: httpx.Response) -> None:
    """Abort before the body is read when the server declares a body larger than the cap."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_IMAGE_BYTES:
        raise httpx.TransportError(f"Declared image size {declared} exceeds {_MAX_IMAGE_BYTES} bytes.")


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=10.0,
        headers={"User-Agent": _USER_AGENT},
        event_hooks={"response": [_reject_oversized_response]},
    )


def _host_resolves_publicly(host: str) -> bool:
    """False if the hostname fails to resolve or resolves to any non-global address (fail closed)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    saw_valid_ip = False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        saw_valid_ip = True
        if not _is_global_ip(ip):
            return False
    return saw_valid_ip


async def _is_fetchable_url(url: str) -> bool:
    if not _is_safe_public_https_url(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    return await anyio.to_thread.run_sync(_host_resolves_publicly, host)


def verify_response_peer_is_public(resp: httpx.Response) -> bool:
    """Verify the actual connected socket peer IP (when exposed by httpx) is globally routable."""
    extensions = getattr(resp, "extensions", None)
    if not isinstance(extensions, Mapping):
        return True
    stream = extensions.get("network_stream")
    if stream is None:
        return True
    get_extra = getattr(stream, "get_extra_info", None)
    if not callable(get_extra):
        return True
    peer = get_extra("peername")
    if not isinstance(peer, tuple) or not peer:
        return True
    raw_ip = str(peer[0]).split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(raw_ip)
    except ValueError:
        return False
    return _is_global_ip(ip)


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
    if not await _is_fetchable_url(url):
        return None
    # Follow redirects manually so every hop gets the same public-host check.
    # The body is streamed and abandoned as soon as it exceeds the size cap.
    status_code = 0
    headers: Optional[Mapping[str, str]] = None
    body = b""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            async with client.stream("GET", current, follow_redirects=False) as resp:
                if not verify_response_peer_is_public(resp):
                    logger.debug("Refusing image response from non-public peer for %s", current)
                    return None
                status_code = resp.status_code
                headers = resp.headers
                if status_code in _REDIRECT_STATUSES:
                    location = str(resp.headers.get("location") or "")
                    next_url = urljoin(current, location)
                    if not location or not await _is_fetchable_url(next_url):
                        logger.debug("Refusing image redirect from %s to %r", url, location)
                        return None
                    current = next_url
                    continue
                if status_code != 200:
                    return None
                declared = str(resp.headers.get("content-length") or "")
                if declared.isdigit() and int(declared) > _MAX_IMAGE_BYTES:
                    return None
                chunks: List[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        return None
                    chunks.append(chunk)
                body = b"".join(chunks)
                break
        except httpx.HTTPError as exc:
            logger.debug("Image download failed for %s: %s", current, exc)
            return None
    else:
        return None
    if headers is None or status_code != 200 or not body:
        return None

    content_type = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    final_path = urlparse(current).path.lower()
    ext = ".jpg"
    if content_type == "image/png" or final_path.endswith(".png"):
        ext = ".png"
    elif content_type == "image/webp" or final_path.endswith(".webp"):
        ext = ".webp"

    upload = UploadFile(filename=f"auto_import{ext}", file=BytesIO(body))
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
    async with _new_client() as client:
        url = await find_image_url(client, name=name, part_number=part_number, category=category)
        if not url:
            return None
        return await download_and_save_image(client, url)


async def find_and_import_images_batch(
    items: List[dict],
) -> Dict[int, str]:
    """Resolve and save photos for multiple items with bounded concurrency and URL deduplication."""
    capped_items = items[:_MAX_BATCH_ITEMS]
    if not capped_items:
        return {}

    results: Dict[int, str] = {}
    url_to_saved: Dict[str, Optional[str]] = {}
    url_events: Dict[str, asyncio.Event] = {}
    query_cache: Dict[tuple[str, Optional[str], Optional[str]], Optional[str]] = {}
    query_events: Dict[tuple[str, Optional[str], Optional[str]], asyncio.Event] = {}
    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def _resolve_one(client: httpx.AsyncClient, item: dict) -> None:
        item_id = int(item["id"])
        name = item.get("name") or ""
        part_number = item.get("serial_or_part_number")
        category = item.get("category")
        qkey = (name, part_number, category)

        if qkey not in query_events:
            evt = asyncio.Event()
            query_events[qkey] = evt
            try:
                async with sem:
                    query_cache[qkey] = await find_image_url(
                        client, name=name, part_number=part_number, category=category
                    )
            finally:
                evt.set()
        else:
            await query_events[qkey].wait()

        url = query_cache.get(qkey)
        if not url:
            return

        if url not in url_events:
            dl_evt = asyncio.Event()
            url_events[url] = dl_evt
            try:
                async with sem:
                    url_to_saved[url] = await download_and_save_image(client, url)
            finally:
                dl_evt.set()
        else:
            await url_events[url].wait()

        saved_path = url_to_saved.get(url)
        if saved_path:
            results[item_id] = saved_path

    async with _new_client() as client:
        await asyncio.gather(*(_resolve_one(client, item) for item in capped_items))
    return results


async def import_image_for_item(bind: Any, item_id: int) -> FindItemImageResponse:
    from app.services.inventory_service import inventory_service

    def _load_item() -> tuple[str, Optional[str], Optional[str]]:
        with session_scope(bind) as thread_db:
            item = inventory_service.get_detail(thread_db, item_id)
            return item.name, item.serial_or_part_number, item.category

    name, part_number, category = await anyio.to_thread.run_sync(_load_item)
    saved_path = await find_and_import_image(name=name, part_number=part_number, category=category)
    if not saved_path:
        raise NotFoundError(f"No photo found online for '{name}'.")

    def _apply_photo() -> ItemRead:
        with session_scope(bind) as thread_db:
            updated = inventory_service.update(thread_db, item_id, ItemUpdate(item_photo=saved_path))
            return inventory_service.to_read_schema(updated, db=thread_db)

    try:
        item_read = await anyio.to_thread.run_sync(_apply_photo)
    except Exception:
        photo_service.delete_upload_url(saved_path)
        raise
    return FindItemImageResponse(status="ok", item_photo=saved_path, item=item_read)


async def import_images_for_location(
    bind: Any,
    location_id: int,
    *,
    force: bool = False,
    include_crops: bool = True,
    batch_lookup_fn: Any = None,
) -> FindLocationItemImagesResponse:
    from app.services.inventory_service import inventory_service
    from app.services.location_service import location_service

    def _collect_candidates() -> list[dict]:
        with session_scope(bind) as thread_db:
            location_service.get_read_by_id(thread_db, location_id)
            loc_items = inventory_service.list_items_for_location(thread_db, location_id)
            return [
                {
                    "id": it.id,
                    "name": it.name,
                    "serial_or_part_number": it.serial_or_part_number,
                    "category": it.category,
                }
                for it in loc_items
                if force or (is_crop_or_missing_photo(it.item_photo) if include_crops else not it.item_photo)
            ]

    candidates = await anyio.to_thread.run_sync(_collect_candidates)
    if not candidates:
        return FindLocationItemImagesResponse(status="ok", checked_count=0, updated_count=0)

    lookup_fn = batch_lookup_fn or find_and_import_images_batch
    resolved = await lookup_fn(candidates)

    def _apply_photos() -> int:
        count = 0
        with session_scope(bind) as thread_db:
            for item_id, saved_path in resolved.items():
                try:
                    inventory_service.update(thread_db, item_id, ItemUpdate(item_photo=saved_path))
                except NotFoundError:
                    photo_service.delete_upload_url(saved_path)
                    continue
                count += 1
        return count

    updated_count = await anyio.to_thread.run_sync(_apply_photos)
    return FindLocationItemImagesResponse(
        status="ok",
        checked_count=len(candidates),
        updated_count=updated_count,
    )
