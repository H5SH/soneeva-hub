"""At most `limit` events per key in a sliding window, per instance."""

import time
from collections import deque


class RateLimit:
    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._seen: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        for k in [k for k, q in self._seen.items() if q and q[-1] <= now - self._window]:
            del self._seen[k]
        seen = self._seen.setdefault(key, deque())
        while seen and seen[0] <= now - self._window:
            seen.popleft()
        if len(seen) >= self._limit:
            return False
        seen.append(now)
        return True
