from src.db import Database
from src.models.auth import Application, Credentials, PublicKey, Tool
from src.models.db import VOICE_KEY, Default
from src.models.mode import Mode, staged, staging_id
from src.services.ttl import MISSING, Ttl

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
        self._cache = Ttl(ttl)
        self._subjects = Ttl(ttl)

    async def get(self, issuer: str) -> Credentials | None:
        cached = self._cache.get(issuer)
        if cached is not MISSING:
            return cached
        found = await self._find(issuer)
        return self._cache.put(issuer, found, None if found else MISS_TTL_SECONDS)

    async def _find(self, issuer: str) -> Credentials | None:
        app_id, mode = staged(issuer)
        row = await self._db.get_row(Default.APPS, app_id)
        if row:
            return Application(**(row | {"id": app_id}))
        row = await self._db.get_row(Default.TOOLS, app_id) if mode is Mode.LIVE else None
        return Tool(**(row | {"id": app_id})) if row else None

    async def subject_key(self, subject: str) -> PublicKey | None:
        """A tool's key for one business, from that business's row; cached like any other."""
        cached = self._subjects.get(subject)
        if cached is not MISSING:
            return cached
        row = await self._db.get_row(Default.BUSINESSES, subject) or {}
        found = PublicKey(**row[VOICE_KEY]) if row.get(VOICE_KEY) else None
        return self._subjects.put(subject, found, None if found else MISS_TTL_SECONDS)

    def forget_subject(self, subject: str) -> None:
        self._subjects.pop(subject)

    def forget(self, app_id: str) -> None:
        """Drop a cached entry and its staging twin — used after every credentials write."""
        self._cache.pop(app_id)
        self._cache.pop(staging_id(app_id))
