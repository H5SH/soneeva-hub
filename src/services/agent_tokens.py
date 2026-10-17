"""Operator tokens for the agent API: short-lived, opaque, identity only.

`kat_<32 random bytes>` is returned once; only its SHA-256 is kept, in `(default)/agentTokens/{hash}`
{uid, expiresAt, createdAt} — `expiresAt` is a TTL field. No scopes ride on the token: the person's
current role is read on every request, so a demotion or a disabled account applies at once.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.db import Database
from src.models.admin import Principal
from src.models.db import Default
from src.services.errors import PlatformError
from src.services.people.rules import holds
from src.services.roles import RoleStore
from src.tunables import tunable

PREFIX = "kat_"
#: The platform (person) scope that may mint and use one; superadmin holds it through `*`.
AGENT_USE = "Agent.Use"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: Any) -> datetime | None:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class AgentTokenService:
    def __init__(self, db: Database, roles: RoleStore) -> None:
        self._db = db
        self._roles = roles

    async def may_use(self, person: Principal) -> bool:
        return holds(await self._roles.raw(person.role), AGENT_USE)

    async def mint(self, person: Principal, minutes: int | None = None) -> dict[str, str]:
        """Returns {token, expiresAt}; the token is never stored or shown again."""
        low, high = tunable("agent.minMinutes"), tunable("agent.maxMinutes")
        minutes = minutes or tunable("agent.defaultMinutes")
        if not low <= minutes <= high:
            raise PlatformError(f"minutes must be between {low} and {high}", 422)
        if not await self.may_use(person):
            raise PlatformError(f"Requires {AGENT_USE}", 403)
        token = PREFIX + secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=minutes)
        await self._db.create_row(Default.AGENT_TOKENS, digest(token),
                                  {"uid": person.uid, "expiresAt": expires, "createdAt": now})
        return {"token": token, "expiresAt": expires.isoformat()}

    async def resolve(self, token: str) -> tuple[str, datetime] | None:
        """(uid, expiresAt) for a live token, else None. TTL deletion is lazy, so expiry is checked here."""
        if not token.startswith(PREFIX):
            return None
        row = await self._db.get_row(Default.AGENT_TOKENS, digest(token))
        expires = _aware(row.get("expiresAt")) if row else None
        if not row or not expires or expires <= datetime.now(UTC):
            return None
        return row["uid"], expires

    async def revoke_all(self, uid: str) -> int:
        rows = await self._db.get_rows(Default.AGENT_TOKENS, filters={"uid": uid})
        for row in rows:
            await self._db.delete_row(Default.AGENT_TOKENS, row["id"])
        return len(rows)
