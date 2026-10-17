import time

from src.db import Database
from src.models.auth import Application, Credentials, Tool
from src.models.db import Default
from src.models.mode import Mode, staged, staging_id

CACHE_TTL_SECONDS = 60.0
MISS_TTL_SECONDS = 10.0


class KeyStore:
    """A caller's credentials by issuer, cached so verification is not a Firestore round trip.

    `apps/{id}` first, then `tools/{id}`; `{appId}_staging` resolves to the app (tools have no twin).
    Misses are cached too, but briefly — otherwise an unknown issuer hammers the database on
    every request, which is exactly what an attacker probing issuer names would produce.
    """

    def __init__(self, db: Database, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._db = db
        self._ttl = ttl
        self._cache: dict[str, tuple[float, Credentials | None]] = {}

    async def get(self, issuer: str) -> Credentials | None:
        cached = self._cache.get(issuer)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        found = await self._find(issuer)
        self._cache[issuer] = (time.monotonic() + (self._ttl if found else MISS_TTL_SECONDS), found)
        return found

    async def _find(self, issuer: str) -> Credentials | None:
        app_id, mode = staged(issuer)
        row = await self._db.get_row(Default.APPS, app_id)
        if row:
            return Application(**(row | {"id": app_id}))
        row = await self._db.get_row(Default.TOOLS, app_id) if mode is Mode.LIVE else None
        return Tool(**(row | {"id": app_id})) if row else None

    def forget(self, app_id: str) -> None:
        """Drop a cached entry and its staging twin — used after every credentials write."""
        self._cache.pop(app_id, None)
        self._cache.pop(staging_id(app_id), None)
