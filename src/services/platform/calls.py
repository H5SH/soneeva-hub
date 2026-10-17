"""Demo-owned: the browser demo's calls in `(default)`: `callers/{callerId}/calls/{id}/messages/{n}`.

The demo writes them in snake_case (the one documented exception to camelCase); this reader is the
only code that touches them, and maps them to the console's camelCase records (records.py).
`callers` None is everyone; otherwise only calls whose `caller_id` is listed.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from src.db import Database, Query, path
from src.models.db import Default, Sub
from src.services.platform.records import COST_PARTS, to_call, to_message

#: Firestore's `in` filter takes at most this many values.
IN_LIMIT = 30
TOTALS = ("calls", "messages", "cost")
#: Most calls read to draw the per-day charts.
RANGE_CAP = 5000


class CallRange(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    before: datetime | None = None


def _chunks(items: list[str]) -> list[list[str]]:
    return [items[i:i + IN_LIMIT] for i in range(0, len(items), IN_LIMIT)]


def _ranged(query: Query, span: CallRange, limit: int) -> Query:
    if span.since:
        query = query.where("started_at", ">=", span.since)
    if span.until:
        query = query.where("started_at", "<=", span.until)
    if span.before:
        query = query.where("started_at", "<", span.before)
    return query.order_by("started_at", descending=True).take(limit)


def _calls(caller: str) -> str:
    return path(Default.CALLERS, caller, Sub.CALLS)


class CallService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _read(self, query: Query) -> list[dict[str, Any]]:
        return [to_call(d.id, d.parent or "", d.data) for d in await self._db.query(query)]

    async def query(self, callers: list[str] | None, span: CallRange, limit: int) -> list[dict[str, Any]]:
        """Newest first."""
        group = Query(Sub.CALLS, group=True)
        if callers is None:
            return await self._read(_ranged(group, span, limit))
        if len(callers) == 1:
            return await self._read(_ranged(Query(_calls(callers[0])), span, limit))
        parts = await asyncio.gather(*[
            self._read(_ranged(group.where("caller_id", "in", c), span, limit)) for c in _chunks(callers)])
        merged = sorted((c for part in parts for c in part), key=lambda c: c["startedAt"] or "", reverse=True)
        return merged[:limit]

    async def get(self, caller: str, call_id: str) -> dict[str, Any] | None:
        doc = await self._db.get(_calls(caller), call_id)
        return to_call(call_id, caller, doc) if doc is not None else None

    async def messages(self, caller: str, call_id: str, after: int | None) -> list[dict[str, Any]]:
        """In order; `after` keeps only those with a higher index."""
        query = Query(path(_calls(caller), call_id, Sub.MESSAGES))
        if after is not None:
            query = query.where("index", ">", after)
        return [to_message(d.id, d.data) for d in await self._db.query(query.order_by("index"))]

    async def _sum(self, query: Query) -> dict[str, float]:
        count, sums = await asyncio.gather(
            self._db.count(query), self._db.sum(query, ["message_count", "cost.total"]))
        return {"calls": float(count), "messages": sums["message_count"], "cost": sums["cost.total"]}

    async def totals(self, callers: list[str] | None) -> dict[str, float]:
        """{calls, messages, cost}, summed by the database."""
        group = Query(Sub.CALLS, group=True)
        if callers is None:
            return await self._sum(group)
        parts = await asyncio.gather(*[
            self._sum(group.where("caller_id", "in", c)) for c in _chunks(callers)])
        return {k: sum(p[k] for p in parts) for k in TOTALS}


def by_day(calls: list[dict[str, Any]], since: datetime, until: datetime) -> list[dict[str, Any]]:
    """One point per UTC day from `since` to `until` (gaps are zero): calls, minutes and each cost part."""
    days: dict[str, dict[str, Any]] = {}
    day = since.astimezone(UTC).date()
    while day <= until.astimezone(UTC).date():
        days[day.isoformat()] = {"day": day.isoformat(), "calls": 0, "minutes": 0.0}
        days[day.isoformat()] |= dict.fromkeys(COST_PARTS, 0.0)
        day += timedelta(days=1)
    for call in calls:
        point = days.get((call["startedAt"] or "")[:10])
        if point is None:
            continue
        point["calls"] += 1
        point["minutes"] += (call["durationSeconds"] or 0) / 60
        for part in COST_PARTS:
            point[part] += (call["cost"] or {}).get(part) or 0
    return [p | {k: round(v, 6) for k, v in p.items() if isinstance(v, float)} for p in days.values()]
