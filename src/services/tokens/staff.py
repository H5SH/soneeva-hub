"""The internal app's callers: an app holding a `hub.payment.*` scope, acting for a platform person
(`uid`, sub `-`) whose role holds the `payment.*` scope asked for. Re-read on every call."""

from fastapi import Depends, HTTPException, Request, status

from src.db import Database
from src.models.admin import Scope
from src.models.identity import Identity
from src.models.mode import staged
from src.services.errors import PlatformError
from src.services.roles import RoleStore
from src.services.state import services_of
from src.services.tokens.caller import enabled_person, verify


async def find_principal(db: Database, roles: RoleStore, uid: str) -> Identity | None:
    """The enabled platform person with their role's `payment.*` scopes, or None."""
    person = await enabled_person(db, uid)
    if not person:
        return None
    return Identity(uid=person.uid, email=person.email, role=person.role,
                     payment_scopes=await roles.scopes(person.role), apps=person.apps,
                     businesses=person.businesses)


async def with_payment(db: Database, roles: RoleStore, person: Identity) -> Identity:
    """`person` with their payment side, when they are an enabled platform person."""
    found = await find_principal(db, roles, person.uid or "")
    if not found:
        return person
    return person.model_copy(update={
        "payment_scopes": found.payment_scopes, "email": found.email, "role": found.role,
        "apps": found.apps, "businesses": found.businesses})


async def principal(request: Request, caller: Identity) -> Identity:
    if caller.business_id or not caller.uid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Call with sub '-' and the person's uid")
    state = services_of(request)
    person = await find_principal(state.db, state.roles, caller.uid)
    if not person:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown or disabled person")
    return person


def requires_staff(app_scope: Scope, person_scope: Scope | None = None):
    async def check(request: Request, caller: Identity = Depends(verify)) -> Identity:
        if str(app_scope) not in caller.app_scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {app_scope}")
        person = await principal(request, caller)
        if person_scope and not person.has(person_scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {person_scope}")
        return person

    return Depends(check)


def need(person: Identity, scope: Scope, app_id: str | None = None) -> None:
    """Scope, and membership of the app when one is named (its staging twin is the same app)."""
    if not person.may(scope, staged(app_id)[0] if app_id else None):
        raise PlatformError(f"Requires {scope}" + (f" on {app_id}" if app_id else ""), 403)
