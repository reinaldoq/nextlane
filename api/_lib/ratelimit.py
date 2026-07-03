import threading
import time
from collections import defaultdict, deque

from fastapi import Depends

from .auth import current_user
from .errors import api_error

# In-memory sliding window, per-instance only: on serverless each cold-started
# instance has its own buckets, so a client can get up to `limit` requests per
# warm instance rather than a single global cap. Acceptable per spec -- this
# is abuse mitigation, not a hard quota -- and keeps the implementation
# dependency-free (no Redis/external store needed for Phase 2).
_buckets: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def reset() -> None:
    with _lock:
        _buckets.clear()


def allow(key: str, limit: int, window: float = 60.0) -> bool:
    now = time.monotonic()
    with _lock:
        q = _buckets[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def rate_limited(limit: int):
    def dep(user: dict = Depends(current_user)) -> dict:
        key = f"{user['sub']}:{limit}"
        if not allow(key, limit):
            raise api_error(429, "rate_limited", "too many requests", headers={"Retry-After": "60"})
        return user

    return dep
