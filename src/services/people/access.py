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
from src.models.people import Actor
from src.models.platform import Caller
from src.services.businesses import BusinessService
from src.services.people.store import People
from src.services.tokens.caller import verify
from src.tunables import tunable


def _strings(value: Any) -> list[str]:
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def grant_ended(until: Any, now: datetime) -> bool:
    if isinstance(until, str) and until:
        until = datetime.fromisoformat(until.replace("Z", "+00:00"))
    return isinstance(until, datetime) and until <= now


async def _role_scopes(bdb: Database, role: Any) -> list[str]:
    row = await bdb.get_row(BusinessDb.ROLES, role) if isinstance(role, str) and role else None
    return _strings((row or {}).get("scopes"))


async def business_scopes(bdb: Database, uid: str, now: datetime | None = None) -> list[str]:
    member = await bdb.get_row(BusinessDb.USERS, uid)
    if member:
        return await _role_scopes(bdb, member.get("role"))
    grant = await bdb.get_row(BusinessDb.GRANTS, uid)
    if not grant or grant.get("blocked") or grant_ended(grant.get("until"), now or datetime.now(UTC)):
        return []
    never = set(tunable("people.grantNever"))
    scopes = _strings(grant.get("scopes")) + await _role_scopes(bdb, grant.get("roleId"))
    return sorted({s for s in scopes if s not in never})


class ActorResolver:
    def __init__(self, people: People, businesses: BusinessService) -> None:
        self._people = people
        self._businesses = businesses

    async def resolve(self, caller: Caller) -> Actor:
        if not caller.uid:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Name the person acting (uid)")
        actor = Actor(app_id=caller.app_id, sub=caller.sub, uid=caller.uid)
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
            actor.role, actor.tokens, actor.apps = profile["role"], profile["tokens"], profile["apps"]
            actor.scopes = roles.get(profile["role"] or "", [])
        return actor


def _app_holds(scope: Scope, caller: Caller, *alternatives: Scope) -> Caller:
    if not {str(s) for s in (scope, *alternatives)} & set(caller.scopes):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {scope}")
    return caller


def requires_app(scope: Scope, *alternatives: Scope) -> Callable:
    """The app's scope (or one of `alternatives`) only, for data about no one (the scope catalogue)."""

    async def check(caller: Caller = Depends(verify)) -> Caller:
        return _app_holds(scope, caller, *alternatives)

    return Depends(check)


def requires_actor(scope: Scope) -> Callable:
    async def check(request: Request, caller: Caller = Depends(verify)) -> Actor:
        return await request.app.state.actors.resolve(_app_holds(scope, caller))

    return Depends(check)
