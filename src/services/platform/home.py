"""/v1/platform/home: karbon-admin's home page as sections, each computed over what the person reaches.

A section is left out when the person's role (or the calling app) may not see it. No revenue anywhere:
payments are counts and details only, demo calls are calls and minutes (costs live on Demo › Price breakdown).
"""

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.models.admin import Principal, Scope
from src.models.mode import Mode
from src.models.people import Actor, Person
from src.models.platform import Business, BusinessStatus, Caller
from src.models.subscription import GatewaySubscription
from src.services.admin import AdminService
from src.services.advertisements import AdvertisementService
from src.services.businesses import BusinessService
from src.services.checklist import MEETING
from src.services.people.rules import Reach, business_reach, holds, reach
from src.services.people.service import PeopleService
from src.services.platform.calls import CallRange
from src.services.platform.data import PlatformData

#: The statuses the home page counts; deleted businesses never reach it.
COUNTED = [s for s in BusinessStatus if s is not BusinessStatus.DELETED]
PLAN_STATUSES = ("active", "soon", "hidden")
#: How many rows each "recent" list holds.
RECENT = 8
DEMO_SCOPE = "Chats.View"
ADS_SCOPES = ("Advertise.View", "Advertise.Write")


@dataclass
class Viewer:
    """Who asks: the app's scopes and the person's role (`caller`), as an actor, and their payment side."""

    caller: Caller
    actor: Actor
    principal: Principal | None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return value if isinstance(value, str) else None


def _newest[T](rows: list[T], key: Callable[[T], str]) -> list[T]:
    return sorted((r for r in rows if key(r)), key=key, reverse=True)[:RECENT]


def businesses(found: list[Business], scope: Reach, names: dict[str, str], days: int, today: date) -> dict:
    """Counts by status and configured, sign-ups per UTC day for `days` days (gaps are zero), the newest."""
    signups = {(today - timedelta(days=n)).isoformat(): 0 for n in reversed(range(days))}
    for b in found:
        day = (_iso(b.createdAt) or "")[:10]
        if day in signups:
            signups[day] += 1
    created = lambda b: _iso(b.createdAt) or ""  # noqa: E731
    return {
        "reach": scope, "total": len(found), "configured": sum(b.configured for b in found),
        **{s.value: sum(b.status is s for b in found) for s in COUNTED},
        "signups": [{"day": day, "count": n} for day, n in signups.items()],
        "recent": [{"id": b.id, "name": b.name, "type": b.type, "status": b.status.value,
                    "servicepersonUid": b.servicepersonUid,
                    "servicepersonName": names.get(b.servicepersonUid or "", b.servicepersonUid),
                    "createdAt": _iso(b.createdAt)} for b in _newest(found, created)],
    }


def assignment(found: list[Business], names: dict[str, str]) -> dict:
    """Unassigned, and businesses per serviceperson (every serviceperson listed)."""
    held = Counter(b.servicepersonUid for b in found if b.servicepersonUid)
    rows = [{"uid": uid, "name": names.get(uid, uid), "businesses": held[uid]} for uid in {*names, *held}]
    return {"unassigned": sum(not b.servicepersonUid for b in found),
            "servicepersons": sorted(rows, key=lambda r: (-r["businesses"], r["name"]))}


def people(found: list[Person], scope: Reach) -> dict:
    """By role, disabled, and the newest and most recently signed-in."""
    card = lambda p: {"uid": p.uid, "name": p.displayName or p.email, "role": p.role,  # noqa: E731
                      "createdAt": p.createdAt, "lastSignInAt": p.lastSignInAt}
    roles = Counter(p.role or "" for p in found)
    return {"reach": scope, "total": len(found), "disabled": sum(p.disabled for p in found),
            "roles": [{"role": r, "count": n} for r, n in roles.most_common()],
            "created": [card(p) for p in _newest(found, lambda p: p.createdAt or "")],
            "active": [card(p) for p in _newest(found, lambda p: p.lastSignInAt or "")]}


def _changed(sub: GatewaySubscription) -> str:
    """When the subscription last changed state that Stripe dates: created or canceled."""
    created = datetime.fromtimestamp(sub.created, UTC) if sub.created else None
    return _iso(max((d for d in (created, sub.canceled_at) if d), default=None)) or ""


class HomeService:
    def __init__(self, businesses: BusinessService, people: PeopleService, ads: AdvertisementService,
                 admin: AdminService, data: PlatformData) -> None:
        self._businesses = businesses
        self._people = people
        self._ads = ads
        self._admin = admin
        self._data = data

    async def home(self, viewer: Viewer, days: int, today: date | None = None) -> dict[str, Any]:
        caller, role, app = viewer.caller, viewer.caller.person_scopes or [], set(viewer.caller.scopes)
        today = today or datetime.now(UTC).date()
        live = [b for b in await self._businesses.every() if b.status is not BusinessStatus.DELETED]
        mine = [b for b in live if caller.reaches(b)]
        sections: dict[str, Any] = {}
        scope = business_reach(role) if Scope.HUB_BUSINESS_VIEW in app else None
        if scope:
            names = {p.uid: p.name for p in await self._people.servicepersons()}
            sections["businesses"] = businesses(mine, scope, names, days, today)
            if scope == "all" and holds(role, "Businesses.Assign"):
                sections["assignment"] = assignment(mine, names)
            if holds(role, "Businesses.Access"):
                own = [b for b in mine if b.servicepersonUid == caller.uid]
                sections["workspace"] = await self._workspace(own)
        users = reach(role, "Users.View") if Scope.HUB_PEOPLE_VIEW in app else None
        if users:
            everyone = await self._people.everyone()
            sections["people"] = people(
                [p for p in everyone if users == "all" or caller.uid in p.servicepersons], users)
        if Scope.HUB_ADVERTISEMENT_VIEW in app and any(holds(role, s) for s in ADS_SCOPES):
            sections["advertisements"] = await self._advertisements({b.id for b in mine})
        if viewer.principal and Scope.HUB_PAYMENT_VIEW in app and viewer.principal.has(Scope.APP_VIEW):
            sections["payments"] = await self._payments(viewer.principal, {b.id: b.name for b in live})
        if Scope.HUB_PLATFORM_VIEW in app and reach(role, DEMO_SCOPE):
            sections["demo"] = await self._demo(viewer.actor, days)
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

    async def _payments(self, principal: Principal, names: dict[str, str]) -> dict:
        """Apps with their plans by status and subscriber count; recent subscription changes. Never money."""
        apps, changes = [], []
        for config, _ in await self._admin.list_apps(principal):
            subs = await self._admin.subscriptions(config.id)
            plans = Counter(p.status for p in config.plans)
            apps.append({"id": config.id, "name": config.name, "plans": {s: plans[s] for s in PLAN_STATUSES},
                         "subscribers": sum(s.is_active for _, s in subs)})
            changes += [(config.id, mode, s) for mode, s in subs
                        if principal.reaches(s.metadata.get("tenant", ""), config.id)]
        recent = [{"appId": app_id, "businessId": s.metadata.get("tenant", ""),
                   "businessName": names.get(s.metadata.get("tenant", "")), "plan": s.plan_code,
                   "status": s.status.value, "test": mode is Mode.TEST, "at": _changed(s)}
                  for app_id, mode, s in changes] if principal.has(Scope.BUSINESS_VIEW) else []
        return {"apps": apps, "recent": sorted(recent, key=lambda r: r["at"], reverse=True)[:RECENT]}

    async def _demo(self, actor: Actor, days: int) -> dict:
        """Calls and minutes per UTC day, over the calls the person reaches."""
        since = datetime.now(UTC) - timedelta(days=days - 1)
        found = await self._data.daily(actor, DEMO_SCOPE, None, CallRange(since=since))
        return {"days": [{"day": d["day"], "calls": d["calls"], "minutes": d["minutes"]} for d in found]}
