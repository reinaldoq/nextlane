import math
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
# Abandoned deques leak, but boundedly (<= limit floats per user per scope)
# and are recycled whenever the instance is.
_buckets: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()

DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60.0


def reset() -> None:
    with _lock:
        _buckets.clear()


def allow(
    key: str, limit: int, window: float = DEFAULT_RATE_LIMIT_WINDOW_SECONDS
) -> tuple[bool, int]:
    """Returns (ok, retry_after): retry_after is the whole seconds until the
    oldest request ages out of the window (0 when allowed)."""
    now = time.monotonic()
    with _lock:
        q = _buckets[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, math.ceil(window - (now - q[0])))
        q.append(now)
        return True, 0


def rate_limited(limit: int, scope: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        key = f"{user['sub']}:{scope}"
        ok, retry_after = allow(key, limit)
        if not ok:
            raise api_error(
                429,
                "rate_limited",
                "too many requests",
                headers={"Retry-After": str(retry_after)},
            )
        return user

    return dep
