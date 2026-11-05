"""/v1/platform/home: karbon-admin's home page as sections, each computed over what the person reaches.

A section is left out when the person's role (or the calling app) may not see it. No revenue anywhere:
payments are counts and details only, demo calls are calls and minutes (costs live on Demo › Price breakdown).
"""

import asyncio
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.models.admin import Scope
from src.models.app import PLAN_STATUSES
from src.models.identity import Identity
from src.models.mode import Mode
from src.models.platform import Business, BusinessStatus
from src.services.admin import AdminService
from src.services.advertisements import AdvertisementService
from src.services.businesses import BusinessService
from src.services.checklist import MEETING
from src.services.people.rules import business_reach, holds, scope_reach
from src.services.people.service import PeopleService
from src.services.platform.calls import CallRange
from src.services.platform.data import PlatformData
from src.services.platform.home_sections import (
    RECENT,
    _changed,
    _iso,
    assignment_section,
    businesses_section,
    people_section,
)

DEMO_SCOPE = "Chats.View"
ADS_SCOPES = ("Advertise.View", "Advertise.Write")


class HomeService:
    def __init__(self, businesses: BusinessService, people: PeopleService, ads: AdvertisementService,
                 admin: AdminService, data: PlatformData) -> None:
        self._businesses = businesses
        self._people = people
        self._ads = ads
        self._admin = admin
        self._data = data

    async def home(self, person: Identity, days: int, today: date | None = None) -> dict[str, Any]:
        role, app = person.role_scopes or [], set(person.app_scopes)
        today = today or datetime.now(UTC).date()
        live = [b for b in await self._businesses.every() if b.status is not BusinessStatus.DELETED]
        mine = [b for b in live if person.reaches_business(b)]
        sections: dict[str, Any] = {}
        scope = business_reach(role) if Scope.HUB_BUSINESS_VIEW in app else None
        if scope:
            names = {p.uid: p.name for p in await self._people.servicepersons()}
            sections["businesses"] = businesses_section(mine, scope, names, days, today)
            if scope == "all" and holds(role, "Businesses.Assign"):
                sections["assignment"] = assignment_section(mine, names)
            if holds(role, "Businesses.Access"):
                own = [b for b in mine if b.servicepersonUid == person.uid]
                sections["workspace"] = await self._workspace(own)
        users = scope_reach(role, "Users.View") if Scope.HUB_PEOPLE_VIEW in app else None
        if users:
            everyone = await self._people.everyone()
            sections["people"] = people_section(
                [p for p in everyone if users == "all" or person.uid in p.servicepersons], users)
        if Scope.HUB_ADVERTISEMENT_VIEW in app and any(holds(role, s) for s in ADS_SCOPES):
            sections["advertisements"] = await self._advertisements({b.id for b in mine})
        if Scope.HUB_PAYMENT_VIEW in app and person.has(Scope.APP_VIEW):
            sections["payments"] = await self._payments(person, {b.id: b.name for b in live})
        if Scope.HUB_PLATFORM_VIEW in app and scope_reach(role, DEMO_SCOPE):
            sections["demo"] = await self._demo(person, days)
        return sections

    async def _workspace(self, assigned: list[Business]) -> dict:
        """The serviceperson's own businesses with onboarding progress, newest first."""
        lists = await asyncio.gather(*(self._businesses.checklist.items(b) for b in assigned))
        rows = []
        for b, items in zip(assigned, lists, strict=True):
            meeting = next((i["state"] for i in items if i["id"] == MEETING), None)
            rows.append({"id": b.id, "name": b.name, "type": b.type, "status": b.status.value,
                         "configured": b.configured, "done": sum(i["state"] == "done" for i in items),
                         "total": len(items), "review": sum(i["state"] == "review" for i in items),
                         "meetingDone": None if meeting is None else meeting == "done",
                         "createdAt": _iso(b.createdAt)})
        return {"businesses": sorted(rows, key=lambda r: r["createdAt"] or "", reverse=True)}

    async def _advertisements(self, reached: set[str]) -> dict:
        """Active and paused; per ad the businesses it reached (of the person's) and how many dismissed it."""
        rows = []
        for ad in await self._ads.every():
            shown = [b for b in ad["businessIds"] if b in reached]
            rows.append({"id": ad["id"], "title": ad["title"], "active": bool(ad["active"]),
                         "businesses": len(shown), "dismissed": await self._ads.dismissed(ad["id"], shown)})
        active = sum(r["active"] for r in rows)
        return {"active": active, "paused": len(rows) - active, "ads": rows}

    async def _payments(self, principal: Identity, names: dict[str, str]) -> dict:
        """Apps with their plans by status and subscriber count; recent subscription changes. Never money."""
        apps, changes = [], []
        by_store = [b.appId for b in await self._businesses.every() if b.storePlan and b.storePlan.is_active]
        for config, _ in await self._admin.list_apps(principal):
            subs = await self._admin.subscriptions(config.id)
            plans = Counter(p.status for p in config.plans)
            apps.append({"id": config.id, "name": config.name, "plans": {s: plans[s] for s in PLAN_STATUSES},
                         "subscribers": sum(s.is_active for _, s in subs) + by_store.count(config.id)})
            changes += [(config.id, mode, s) for mode, s in subs
                        if principal.reaches_id(s.metadata.get("tenant", ""), config.id)]
        recent = [{"appId": app_id, "businessId": s.metadata.get("tenant", ""),
                   "businessName": names.get(s.metadata.get("tenant", "")), "plan": s.plan_code,
                   "status": s.status.value, "test": mode is Mode.TEST, "at": _changed(s)}
                  for app_id, mode, s in changes] if principal.has(Scope.BUSINESS_VIEW) else []
        return {"apps": apps, "recent": sorted(recent, key=lambda r: r["at"], reverse=True)[:RECENT]}

    async def _demo(self, actor: Identity, days: int) -> dict:
        """Calls and minutes per UTC day, over the calls the person reaches."""
        since = datetime.now(UTC) - timedelta(days=days - 1)
        found = await self._data.daily(actor, DEMO_SCOPE, None, CallRange(since=since))
        return {"days": [{"day": d["day"], "calls": d["calls"], "minutes": d["minutes"]} for d in found]}
