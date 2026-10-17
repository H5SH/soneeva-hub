"""The onboarding checklist in `b-{id}/checklist`: seeded at sign-up, some items kept by the hub.

The console moves the other items (serviceperson sends for review, owner marks done). The hub
owns `checklistAuto` (assigned, subscribed) and lets the assigned serviceperson tick `meeting`.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from src.db import Database
from src.models.db import BusinessDb, Default
from src.models.platform import Business, Caller
from src.services.errors import PlatformError
from src.services.people.rules import holds
from src.tunables import tunable

#: `by` on items the hub changes itself.
HUB_ACTOR = "karbon-hub"
MEETING = "meeting"


def items_for(business_type: str) -> list[str]:
    """`checklistStart` first, for every type, then the type's own checklist."""
    kind = tunable("businesses.checklistByType").get(business_type, "default")
    start = tunable("businesses.checklistStart")
    return start + [i for i in tunable("businesses.checklists")[kind] if i not in start]


def new_item(item: str, order: int) -> dict[str, Any]:
    label = tunable("businesses.checklistLabels").get(item, item)
    return {"label": label, "state": "open", "order": order, "by": None, "at": None}


class ChecklistService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def seed(self, bdb: Database, business_type: str) -> None:
        """Insert only, so a re-run keeps what has moved since."""
        await asyncio.gather(*(bdb.create_row(BusinessDb.CHECKLIST, item, new_item(item, order))
                               for order, item in enumerate(items_for(business_type))))

    async def _bdb(self, business_id: str) -> Database | None:
        row = await self._db.get_row(Default.BUSINESSES, business_id)
        return self._db.open(row["dbId"]) if row and row.get("dbId") else None

    async def set_state(self, business_id: str, item: str, done: bool, by: str = HUB_ACTOR) -> bool:
        """Done or open again; False when the business or item does not exist or nothing changed."""
        bdb = await self._bdb(business_id)
        row = await bdb.get_row(BusinessDb.CHECKLIST, item) if bdb else None
        state = "done" if done else "open"
        if not bdb or not row or row.get("state") == state:
            return False
        await bdb.set_row(BusinessDb.CHECKLIST, item, {"state": state, "by": by, "at": datetime.now(UTC)})
        return True

    async def on_subscription(self, business_id: str, active: bool) -> None:
        """Called for every saved subscription; a store that is no business is skipped."""
        await self.set_state(business_id, "subscribed", active)

    async def tick_meeting(self, caller: Caller, business: Business, done: bool) -> dict[str, Any]:
        """The assigned serviceperson, or a platform person holding Businesses.Write."""
        assigned = caller.assigned_to(business)
        write = caller.person_scopes is not None and holds(caller.person_scopes, "Businesses.Write")
        if caller.business_id or not (assigned or write):
            raise PlatformError("Only the assigned serviceperson or Businesses.Write", 403)
        bdb = self._db.open(business.dbId)
        if not await bdb.get_row(BusinessDb.CHECKLIST, MEETING):
            raise PlatformError("This business has no meeting item", 409)
        await self.set_state(business.id, MEETING, done, caller.uid or caller.app_id)
        return {"id": MEETING, "state": "done" if done else "open"}

    async def items(self, business: Business) -> list[dict[str, Any]]:
        """In order, as `{id, label, state, by, at}` (`at` ISO or null)."""
        rows = await self._db.open(business.dbId).get_rows(BusinessDb.CHECKLIST)
        rows.sort(key=lambda r: (r.get("order", float("inf")), r["id"]))
        at = lambda v: v.isoformat() if isinstance(v, datetime) else v  # noqa: E731
        return [{"id": r["id"], "label": r.get("label", r["id"]), "state": r.get("state", "open"),
                 "by": r.get("by"), "at": at(r.get("at"))} for r in rows]
