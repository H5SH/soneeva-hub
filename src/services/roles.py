"""Role → scopes, cached.

Read on every authenticated request to resolve a principal's scopes, and changed about as
often as someone edits a role. Same shape as KeyStore: a short TTL, plus an explicit drop on
write so an edit takes effect at once on the instance that made it.
"""

from src.db import Database
from src.models.admin import DEFAULT_ROLES, ROLE_SCOPES, Scope
from src.models.db import Default
from src.services.ttl import MISSING, Ttl
from src.tunables import tunable

CACHE_TTL_SECONDS = 60.0


class RoleStore:
    def __init__(self, db: Database, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._db = db
        self._rows = Ttl(ttl)

    async def _stored(self, role: str) -> list[str] | None:
        """The role's stored scopes, cached; None when it has none stored."""
        scopes = self._rows.get(role)
        if scopes is MISSING:
            row = await self._db.get_row(Default.ROLES, role) or {}
            stored = row.get("scopes")
            scopes = self._rows.put(role, stored if isinstance(stored, list) else None)
        return scopes

    async def scopes(self, role: str) -> list[Scope]:
        stored = await self._stored(role) if role else []
        if stored is None:
            return list(DEFAULT_ROLES.get(role, []))
        return [Scope(s) for s in stored if s in set(ROLE_SCOPES)]

    async def raw(self, role: str) -> list[str]:
        """The role's person scopes as stored: console ids (`*` = every one), `payment.*`, `platform.*`."""
        stored = await self._stored(role) if role else []
        if stored is None:
            return [str(s) for s in DEFAULT_ROLES.get(role, [])] + tunable("platformRoles").get(role, [])
        return stored

    def forget(self, role: str) -> None:
        self._rows.pop(role)
