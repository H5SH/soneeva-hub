import time

from app.models.auth import Application
from app.models.db import Collection
from app.services.db.base import Database

CACHE_TTL_SECONDS = 60.0
MISS_TTL_SECONDS = 10.0


class KeyStore:
    """Reads applications/{id} and caches it, so verification is not a Firestore round trip.

    Misses are cached too, but briefly — otherwise an unknown issuer hammers the database on
    every request, which is exactly what an attacker probing issuer names would produce.
    """

    def __init__(self, db: Database, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._db = db
        self._ttl = ttl
        self._cache: dict[str, tuple[float, Application | None]] = {}

    async def get(self, application_id: str) -> Application | None:
        cached = self._cache.get(application_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        row = await self._db.get_row(Collection.APPLICATIONS, application_id)
        application = Application(**(row | {"id": application_id})) if row else None
        ttl = self._ttl if application else MISS_TTL_SECONDS
        self._cache[application_id] = (time.monotonic() + ttl, application)
        return application

    def forget(self, application_id: str) -> None:
        """Drop a cached entry — used after a key rotation writes."""
        self._cache.pop(application_id, None)
