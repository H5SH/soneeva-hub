"""The sections of the platform home page."""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.models.people import Person
from src.models.platform import Business, BusinessStatus
from src.models.subscription import GatewaySubscription
from src.services.people.rules import Reach

#: The statuses the home page counts; deleted businesses never reach it.
COUNTED = [s for s in BusinessStatus if s is not BusinessStatus.DELETED]


#: How many rows each "recent" list holds.
RECENT = 8


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return value if isinstance(value, str) else None


def _newest[T](rows: list[T], key: Callable[[T], str]) -> list[T]:
    return sorted((r for r in rows if key(r)), key=key, reverse=True)[:RECENT]


def businesses_section(found: list[Business], scope: Reach, names: dict[str, str], days: int,
                       today: date) -> dict:
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


def assignment_section(found: list[Business], names: dict[str, str]) -> dict:
    """Unassigned, and businesses per serviceperson (every serviceperson listed)."""
    held = Counter(b.servicepersonUid for b in found if b.servicepersonUid)
    rows = [{"uid": uid, "name": names.get(uid, uid), "businesses": held[uid]} for uid in {*names, *held}]
    return {"unassigned": sum(not b.servicepersonUid for b in found),
            "servicepersons": sorted(rows, key=lambda r: (-r["businesses"], r["name"]))}


def people_section(found: list[Person], scope: Reach) -> dict:
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
