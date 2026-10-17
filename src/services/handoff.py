"""Serviceperson handoff: karbon-admin asks for a one-time code, the console redeems it.

`(default)/handoffs/{code}` holds {uid, businessId, expireAt, used}; `expireAt` is a TTL field.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.auth import AccountProvider
from src.db import Database
from src.models.db import BusinessDb, Default
from src.models.people import Actor
from src.models.platform import Caller
from src.services.businesses import BusinessService, audit
from src.services.errors import PlatformError
from src.services.people.access import grant_ended
from src.services.people.rules import holds

CONSOLE_APP = "soneeva"
ACCESS_SCOPE = "Businesses.Access"
CODE_SECONDS = 60


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value or None


class HandoffService:
    def __init__(self, db: Database, businesses: BusinessService, accounts: AccountProvider,
                 console_url: str) -> None:
        self._db = db
        self._businesses = businesses
        self._accounts = accounts
        self._console_url = console_url.rstrip("/")

    async def _grant(self, business_id: str, uid: str, now: datetime) -> dict[str, Any]:
        bdb = await self._businesses.database(business_id)
        if not bdb:
            raise PlatformError("No such business", 404)
        grant = await bdb.get_row(BusinessDb.GRANTS, uid)
        if not grant or grant.get("blocked") or grant_ended(grant.get("until"), now):
            raise PlatformError("No running grant for this business", 403)
        return grant

    async def create(self, actor: Actor, business_id: str) -> dict[str, str]:
        if actor.business_id:
            raise PlatformError("Handoff is called with sub '-'", 403)
        if not actor.platform or not holds(actor.scopes, ACCESS_SCOPE):
            raise PlatformError(f"Requires {ACCESS_SCOPE}", 403)
        now = datetime.now(UTC)
        await self._grant(business_id, actor.uid, now)
        code = secrets.token_urlsafe(32)
        await self._db.create_row(Default.HANDOFFS, code, {
            "uid": actor.uid, "businessId": business_id, "used": False,
            "expireAt": now + timedelta(seconds=CODE_SECONDS),
        })
        await audit(self._db, "handoff.create", business_id, actor.uid, {})
        return {"url": f"{self._console_url}/handoff?code={code}"}

    async def redeem(self, caller: Caller, code: str) -> dict[str, Any]:
        """Once only (410 after), before `expireAt` (410), for the console, with the grant re-read."""
        if caller.app_id != CONSOLE_APP:
            raise PlatformError("Only the console redeems a handoff", 403)
        row = await self._db.get_row(Default.HANDOFFS, code)
        if not row:
            raise PlatformError("No such handoff", 404)
        business_id, uid = row["businessId"], row["uid"]
        if caller.business_id and caller.business_id != business_id:
            raise PlatformError("This handoff is for another business", 403)
        now = datetime.now(UTC)
        if row.get("used") or row["expireAt"] <= now:
            raise PlatformError("This handoff is used or expired", 410)
        if not await self._db.claim(Default.HANDOFFS, code, "used", {"usedAt": now}):
            raise PlatformError("This handoff is used or expired", 410)
        grant = await self._grant(business_id, uid, now)
        account = await self._accounts.get(uid)
        await audit(self._db, "handoff.redeem", business_id, uid, {})
        return {
            "uid": uid, "businessId": business_id,
            "displayName": account.displayName if account else "", "email": account.email if account else "",
            "grant": {"scopes": [s for s in grant.get("scopes") or [] if isinstance(s, str)],
                      "roleId": grant.get("roleId") or None, "until": _iso(grant.get("until")),
                      "blocked": bool(grant.get("blocked"))},
        }
