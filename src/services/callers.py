"""Callers in a business database: `callers/{e164}`, written by soneeva-voice on every call.

soneeva-voice merges only its own fields (phone, role, businessId, branchId, anonymous,
lastCallAt); people add `name`, `nameLower` (the search key), `tags`, `updatedBy`,
`updatedAt` and `blocked`, `blockedReason`, `blockedBy`, `blockedAt` (soneeva-voice rejects a
blocked number before anything else). Withheld numbers (`anon-{callSid}`) are never listed.
Every query here is served by Firestore's automatic single-field indexes.
"""

import asyncio
from typing import Any

from src.db import DELETE, ID, Database, Query, business_db, path
from src.models.callers import CallerQuery
from src.models.db import BusinessDb, Sub
from src.models.platform import Business
from src.services.platform.records import iso

ANONYMOUS_PREFIX = "anon-"
#: The end of a prefix range.
HIGH = ""


def to_caller(phone: str, d: dict[str, Any], calls: int) -> dict[str, Any]:
    tags = d.get("tags")
    return {
        "phone": phone,
        "name": d.get("name") if isinstance(d.get("name"), str) else "",
        "tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
        "lastCallAt": iso(d.get("lastCallAt")),
        "branchId": d.get("branchId") or None,
        "calls": calls,
        "updatedAt": iso(d.get("updatedAt")),
        "updatedBy": d.get("updatedBy") or None,
        "blocked": d.get("blocked") is True,
        "blockedReason": d.get("blockedReason") or None,
        "blockedBy": d.get("blockedBy") or None,
        "blockedAt": iso(d.get("blockedAt")),
    }


def _query(query: CallerQuery) -> Query:
    callers, number = Query(BusinessDb.CALLERS), query.number
    if query.blocked:
        return callers.where("blocked", "==", True).order_by(ID)
    if query.tag:
        return callers.where("tags", "array_contains", query.tag).order_by(ID)
    if number:
        return callers.where("phone", ">=", number).where("phone", "<=", number + HIGH).order_by("phone")
    if query.q:
        key = query.q.lower()
        return (callers.where("nameLower", ">=", key).where("nameLower", "<=", key + HIGH)
                .order_by("nameLower"))
    return callers.order_by("lastCallAt", descending=True)


class CallerService:
    """Each call names the business; its data is wherever its `dbId` says (src/db/place.py)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def _calls(self, bdb: Database, phone: str) -> int:
        return await bdb.count(Query(path(BusinessDb.CALLERS, phone, Sub.CALLS)))

    async def search(self, business: Business, query: CallerQuery) -> tuple[list[dict[str, Any]], str | None]:
        """A page of callers and the cursor after it (None at the end).

        `tag`: that tag, by number. `blocked`: blocked ones, by number. A numeric `q`: phone
        prefix. Other `q`: name prefix. None of these: most recent call first. `before` is the
        previous page's cursor.
        """
        bdb = business_db(self._db, business.id, business.dbId)
        found = _query(query).take(query.limit + 1)
        docs = await bdb.query(found.start_after(query.before) if query.before else found)
        page = docs[:query.limit]
        listed = [d for d in page if not d.id.startswith(ANONYMOUS_PREFIX)]
        counts = await asyncio.gather(*[self._calls(bdb, d.id) for d in listed])
        rows = [to_caller(d.id, d.data, n) for d, n in zip(listed, counts, strict=True)]
        return rows, (page[-1].id if len(docs) > query.limit else None)

    async def get(self, business: Business, phone: str) -> dict[str, Any] | None:
        bdb = business_db(self._db, business.id, business.dbId)
        doc = await bdb.get(BusinessDb.CALLERS, phone)
        return None if doc is None else to_caller(phone, doc, await self._calls(bdb, phone))

    async def update(self, business: Business, phone: str, fields: dict[str, Any],
                     create: bool = False) -> dict[str, Any] | None:
        """Merge `fields` (None deletes that field) into a caller; None when there is no such
        caller, unless `create` makes it (`phone` plus `fields`: a number that never called)."""
        bdb = business_db(self._db, business.id, business.dbId)
        exists = await bdb.get(BusinessDb.CALLERS, phone) is not None
        if not exists and not create:
            return None
        if exists:
            await bdb.set(BusinessDb.CALLERS, phone,
                          {k: DELETE if v is None else v for k, v in fields.items()})
        else:  # merged all the same, in case a call created it meanwhile
            await bdb.set(BusinessDb.CALLERS, phone,
                          {"phone": phone} | {k: v for k, v in fields.items() if v is not None})
        return await self.get(business, phone)
