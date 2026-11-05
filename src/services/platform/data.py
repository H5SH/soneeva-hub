"""A console's scope catalogue and the phone demo's calls."""

from datetime import UTC, datetime, timedelta
from typing import Any

from src.db import Database
from src.models.db import Default
from src.models.identity import Identity
from src.services.people.rules import Refused, scope_reach
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
    def _sees_calls(self, actor: Identity, scope: str) -> bool:
        """Whether `scope` shows this person calls at all. The callers are phone numbers, nobody's own, so
        only an unrestricted reach sees any; a scope that doesn't read calls, or isn't held, is refused."""
        if scope not in tunable("people.callScopes"):
            raise Refused(f"{scope} does not read calls", 422)
        held = scope_reach(actor.scopes, scope) if actor.platform else None
        if held is None:
            raise Refused(f"Requires {scope}")
        return held == "all"

    async def calls(self, actor: Identity, scope: str, caller: str | None, span: CallRange,
                    limit: int, engine: str | None = None) -> dict[str, Any]:
        """`engine` keeps one agent's calls of each page (a page may come back shorter)."""
        if not self._sees_calls(actor, scope):
            return {"calls": [], "next": None}
        found = await self._calls.query([caller] if caller else None, span, limit + 1)
        page = found[:limit]
        nxt = page[-1]["startedAt"] if len(found) > limit and page else None
        return {"calls": [c for c in page if not engine or c["engine"] == engine], "next": nxt}

    async def call(self, actor: Identity, scope: str, caller: str, call_id: str) -> dict[str, Any]:
        if not self._sees_calls(actor, scope):
            raise Refused("No such call", 404)
        found = await self._calls.get(caller, call_id)
        if not found:
            raise Refused("No such call", 404)
        return found

    async def messages(self, actor: Identity, scope: str, caller: str, call_id: str,
                       after: int | None) -> list[dict[str, Any]]:
        if not self._sees_calls(actor, scope):
            raise Refused("No such call", 404)
        return await self._calls.messages(caller, call_id, after)

    async def totals(self, actor: Identity, scope: str, callers: list[str] | None) -> dict[str, float]:
        if not self._sees_calls(actor, scope):
            return {"calls": 0, "messages": 0, "cost": 0}
        return await self._calls.totals(callers)

    async def daily(self, actor: Identity, scope: str, caller: str | None,
                    span: CallRange, engine: str | None = None) -> list[dict[str, Any]]:
        """Per UTC day over `span` (default: the last 30 days): calls, minutes and cost by part, of one
        agent's calls when `engine` names it."""
        until = span.until or datetime.now(UTC)
        since = span.since or until - timedelta(days=tunable("people.callDays") - 1)
        span = CallRange(since=since, until=until)
        callers = [caller] if caller else None
        found = await self._calls.query(callers, span, RANGE_CAP) if self._sees_calls(actor, scope) else []
        return by_day([c for c in found if not engine or c["engine"] == engine], since, until)
