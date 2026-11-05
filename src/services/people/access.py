"""Who acts on /v1/accounts, /v1/people, /v1/roles, /v1/platform: the app's hub scope, then the named person.

With sub `-` the person's scopes are their platform role's (console ids). With sub = businessId
they are their member role's in that business, or their serviceperson grant's while it runs.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from src.db import Database
from src.models.admin import Scope
from src.models.db import BusinessDb
from src.models.identity import Identity
from src.models.platform import Business
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.people.rules import CANNOT_READ, reads_business
from src.services.people.store import People, strings
from src.services.state import services_of
from src.services.tokens.caller import verify
from src.tunables import tunable


def grant_ended(until: Any, now: datetime) -> bool:
    if isinstance(until, str) and until:
        until = datetime.fromisoformat(until.replace("Z", "+00:00"))
    return isinstance(until, datetime) and until <= now


async def _role_scopes(bdb: Database, role: Any) -> list[str]:
    row = await bdb.get_row(BusinessDb.ROLES, role) if isinstance(role, str) and role else None
    return strings((row or {}).get("scopes"))


async def business_scopes(bdb: Database, uid: str, now: datetime | None = None) -> list[str]:
    member = await bdb.get_row(BusinessDb.USERS, uid)
    if member:
        return await _role_scopes(bdb, member.get("role"))
    grant = await bdb.get_row(BusinessDb.GRANTS, uid)
    if not grant or grant.get("blocked") or grant_ended(grant.get("until"), now or datetime.now(UTC)):
        return []
    never = set(tunable("people.grantNever"))
    scopes = strings(grant.get("scopes")) + await _role_scopes(bdb, grant.get("roleId"))
    return sorted({s for s in scopes if s not in never})


async def held_in(businesses: BusinessService, business: Business, caller: Identity) -> set[str]:
    """What the person the business token names holds there."""
    return set(await business_scopes(businesses.business_db(business), caller.uid)) if caller.uid else set()


async def member_or_reader(businesses: BusinessService, caller: Identity, business_id: str,
                           scopes: set[str]) -> Business:
    """The business, for a member holding one of `scopes`, or a platform person who `reads_business`."""
    business = await businesses.require(caller, business_id)
    if caller.business_id:
        if not scopes & await held_in(businesses, business, caller):
            raise PlatformError(f"Requires {' or '.join(sorted(scopes))}", 403)
    elif not reads_business(caller, business):
        raise PlatformError(CANNOT_READ, 403)
    return business


class ActorResolver:
    def __init__(self, people: People, businesses: BusinessService) -> None:
        self._people = people
        self._businesses = businesses

    async def resolve(self, caller: Identity) -> Identity:
        if not caller.uid:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Name the person acting (uid)")
        actor = caller
        if caller.business_id:
            bdb = await self._businesses.database(caller.business_id)
            if not bdb:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such business")
            actor.scopes = await business_scopes(bdb, caller.uid)
            return actor
        profile = await self._people.profile(caller.uid)
        if profile and profile["disabled"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Disabled person")
        if profile:
            roles = await self._people.roles()
            actor.platform = True
            actor.role, actor.tokens = profile["role"], profile["tokens"]
            actor.apps, actor.businesses = profile["apps"], profile["businesses"]
            actor.scopes = roles.get(profile["role"] or "", [])
        return actor


def _app_holds(scope: Scope, caller: Identity, *alternatives: Scope) -> Identity:
    if not {str(s) for s in (scope, *alternatives)} & set(caller.app_scopes):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {scope}")
    return caller


def requires_app(scope: Scope, *alternatives: Scope) -> Callable:
    """The app's scope (or one of `alternatives`) only, for data about no one (the scope catalogue)."""

    async def check(caller: Identity = Depends(verify)) -> Identity:
        return _app_holds(scope, caller, *alternatives)

    return Depends(check)


def requires_actor(scope: Scope) -> Callable:
    async def check(request: Request, caller: Identity = Depends(verify)) -> Identity:
        return await services_of(request).actors.resolve(_app_holds(scope, caller))

    return Depends(check)
