"""Role → scopes, cached.

Read on every authenticated request to resolve a principal's scopes, and changed about as
often as someone edits a role. Same shape as KeyStore: a short TTL, plus an explicit drop on
write so a dashboard edit takes effect at once on the instance that made it.
"""

import time

from app.models.admin import DEFAULT_ROLES, Scope
from app.models.db import Collection
from app.services.db import Database

CACHE_TTL_SECONDS = 60.0


class RoleStore:
    def __init__(self, db: Database, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._db = db
        self._ttl = ttl
        self._cache: dict[str, tuple[float, list[Scope]]] = {}

    async def scopes(self, role: str) -> list[Scope]:
        if not role:
            return []
        cached = self._cache.get(role)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        row = await self._db.get_row(Collection.ROLES, role)
        if row and isinstance(row.get("scopes"), list):
            known = set(Scope)
            scopes = [Scope(s) for s in row["scopes"] if s in known]
        else:
            scopes = list(DEFAULT_ROLES.get(role, []))
        self._cache[role] = (time.monotonic() + self._ttl, scopes)
        return scopes

    def forget(self, role: str) -> None:
        self._cache.pop(role, None)
