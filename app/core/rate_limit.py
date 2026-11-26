from collections import deque
import math
import re
import threading
import time
from typing import Dict, Optional, Tuple
from app.core.config import settings

_VISION_ANALYZE_RE = re.compile(r"^/api/v1/locations/\d+/analyze-photo$")
_ITEM_FIND_IMAGE_RE = re.compile(r"^/api/v1/items/\d+/find-image$")
_LOC_FIND_IMAGES_RE = re.compile(r"^/api/v1/locations/\d+/find-item-images$")
_LOC_PHOTOS_RE = re.compile(r"^/api/v1/locations/\d+/photos(?:/attach)?$")
_ITEM_ATTACHMENTS_RE = re.compile(r"^/api/v1/items/\d+/attachments(?:/from-photos-pdf)?$")
_CAPTURE_UPLOAD_RE = re.compile(r"^/api/v1/photos/capture-sessions/[A-Za-z0-9_\-]+/upload$")


class SlidingWindowRateLimiter:
    """Thread-safe in-memory per-IP sliding-window rate limiter."""

    MAX_KEYS = 10_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: Dict[Tuple[str, str], deque[float]] = {}

    def check(
        self,
        client_ip: str,
        bucket: str,
        limit: int,
        window_seconds: float = 60.0,
    ) -> Tuple[bool, int]:
        if limit <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_seconds
        key = (client_ip or "unknown", bucket)

        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                if len(self._hits) >= self.MAX_KEYS:
                    self._prune_stale_locked(cutoff)
                dq = deque()
                self._hits[key] = dq

            while dq and dq[0] <= cutoff:
                dq.popleft()

            if len(dq) >= limit:
                retry_after = max(1, math.ceil(dq[0] + window_seconds - now))
                return False, retry_after

            dq.append(now)
            return True, 0

    def _prune_stale_locked(self, cutoff: float) -> None:
        stale_keys = [
            k for k, dq in self._hits.items()
            if not dq or dq[-1] <= cutoff
        ]
        for k in stale_keys:
            del self._hits[k]
        if len(self._hits) >= self.MAX_KEYS:
            # Drop oldest entries if still over capacity under heavy distinct-IP churn.
            drop_count = len(self._hits) - (self.MAX_KEYS // 2)
            for k in list(self._hits.keys())[:drop_count]:
                del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def match_rate_limit_bucket(method: str, path: str) -> Optional[Tuple[str, int]]:
    if method.upper() != "POST":
        return None
    if path == "/api/v1/auth/login":
        return ("auth", settings.RATE_LIMIT_AUTH_PER_MIN)
    if path == "/api/v1/search/photo" or _VISION_ANALYZE_RE.match(path):
        return ("vision", settings.RATE_LIMIT_VISION_PER_MIN)
    if (
        path in ("/api/v1/system/backup", "/api/v1/system/cleanup-unused-crops")
        or _ITEM_FIND_IMAGE_RE.match(path)
        or _LOC_FIND_IMAGES_RE.match(path)
    ):
        return ("system", settings.RATE_LIMIT_SYSTEM_PER_MIN)
    if (
        path in ("/api/v1/photos/upload", "/api/v1/photos/capture-sessions", "/api/v1/unit-setup/sessions")
        or _CAPTURE_UPLOAD_RE.match(path)
        or _LOC_PHOTOS_RE.match(path)
        or _ITEM_ATTACHMENTS_RE.match(path)
    ):
        return ("upload", settings.RATE_LIMIT_UPLOAD_PER_MIN)
    return None


rate_limiter = SlidingWindowRateLimiter()
