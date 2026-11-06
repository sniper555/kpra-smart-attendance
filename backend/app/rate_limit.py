import threading
import time
from collections import defaultdict

from app.errors import AppError


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._hits[key] if now - stamp < window_seconds]
            if len(recent) >= limit:
                self._hits[key] = recent
                raise AppError(
                    "RATE_LIMITED",
                    "Too many attempts. Wait a few minutes and try again.",
                    429,
                )
            recent.append(now)
            self._hits[key] = recent


limiter = RateLimiter()
