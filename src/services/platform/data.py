"""A console's scope catalogue and the phone demo's calls."""

from datetime import UTC, datetime, timedelta
from typing import Any

from src.db import Database
from src.models.db import Default
from src.models.people import Actor
from src.services.people.rules import Refused, reach
from src.services.platform.calls import RANGE_CAP, CallRange, CallService, by_day
from src.tunables import tunable


class PlatformData:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._calls = CallService(db)

    async def scopes(self, app_id: str) -> list[dict[str, Any]]:
        row = await self._db.get_row(Default.SCOPES, app_id)
        return list((row or {}).get("scopes") or [])

    # ── demo calls ──────────────────────────────────────────────────────────
    async def _callers(self, actor: Actor, scope: str, asked: list[str] | None) -> list[str] | None:
        """Whose calls this person may read through `scope`: None is everyone. The callers are phone
        numbers, nobody's own, so only an unrestricted reach sees any."""
        if scope not in tunable("people.callScopes"):
            raise Refused(f"{scope} does not read calls", 422)
        r = reach(actor.scopes, scope) if actor.platform else None
        if r is None:
            raise Refused(f"Requires {scope}")
        return asked if r == "all" else []

    async def calls(self, actor: Actor, scope: str, caller: str | None, span: CallRange,
                    limit: int, engine: str | None = None) -> dict[str, Any]:
        """`engine` keeps one agent's calls of each page (a page may come back shorter)."""
        callers = await self._callers(actor, scope, [caller] if caller else None)
        if callers == []:
            return {"calls": [], "next": None}
        found = await self._calls.query(callers, span, limit + 1)
        page = found[:limit]
        nxt = page[-1]["startedAt"] if len(found) > limit and page else None
        return {"calls": [c for c in page if not engine or c["engine"] == engine], "next": nxt}

    async def call(self, actor: Actor, scope: str, caller: str, call_id: str) -> dict[str, Any]:
        if await self._callers(actor, scope, [caller]) == []:
            raise Refused("No such call", 404)
        found = await self._calls.get(caller, call_id)
        if not found:
            raise Refused("No such call", 404)
        return found

    async def messages(self, actor: Actor, scope: str, caller: str, call_id: str,
                       after: int | None) -> list[dict[str, Any]]:
        if await self._callers(actor, scope, [caller]) == []:
            raise Refused("No such call", 404)
        return await self._calls.messages(caller, call_id, after)

    async def totals(self, actor: Actor, scope: str, callers: list[str] | None) -> dict[str, float]:
        allowed = await self._callers(actor, scope, callers)
        if allowed == []:
            return {"calls": 0, "messages": 0, "cost": 0}
        return await self._calls.totals(allowed)

    async def daily(self, actor: Actor, scope: str, caller: str | None,
                    span: CallRange, engine: str | None = None) -> list[dict[str, Any]]:
        """Per UTC day over `span` (default: the last 30 days): calls, minutes and cost by part, of one
        agent's calls when `engine` names it."""
        until = span.until or datetime.now(UTC)
        since = span.since or until - timedelta(days=tunable("people.callDays") - 1)
        callers = await self._callers(actor, scope, [caller] if caller else None)
        span = CallRange(since=since, until=until)
        found = [] if callers == [] else await self._calls.query(callers, span, RANGE_CAP)
        return by_day([c for c in found if not engine or c["engine"] == engine], since, until)
