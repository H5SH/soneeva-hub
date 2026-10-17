"""A per-process cache whose entries expire. Each hub instance keeps its own; a TTL bounds how stale
another instance may be after a write or webhook landed elsewhere."""

import time
from typing import Any

MISSING: Any = object()


class Ttl:
    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._entries: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any:
        """The value, or MISSING when absent or expired."""
        entry = self._entries.get(key)
        if not entry or entry[0] <= time.monotonic():
            return MISSING
        return entry[1]

    def put(self, key: Any, value: Any) -> Any:
        now = time.monotonic()
        if len(self._entries) > 10_000:
            self._entries = {k: e for k, e in self._entries.items() if e[0] > now}
        self._entries[key] = (now + self._seconds, value)
        return value

    def pop(self, key: Any) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
