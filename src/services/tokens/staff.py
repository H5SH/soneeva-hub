"""The internal app's callers: an app holding a `hub.payment.*` scope, acting for a platform person
(`uid`, sub `-`) whose role holds the `payment.*` scope asked for. Re-read on every call."""

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from src.models.admin import Principal, Scope, UserRecord
from src.models.db import Default
from src.models.mode import staged
from src.models.platform import Caller
from src.services.errors import PlatformError
from src.services.tokens.caller import verify


async def find_principal(state: Any, uid: str) -> Principal | None:
    """The enabled platform person with their role's `payment.*` scopes, or None."""
    row = await state.db.get_row(Default.USERS, uid)
    person = UserRecord(**(row | {"uid": uid})) if row else None
    if not person or person.disabled:
        return None
    return Principal(uid=person.uid, email=person.email, role=person.role,
                     scopes=await state.roles.scopes(person.role), apps=person.apps,
                     businesses=person.businesses)


async def principal(request: Request, caller: Caller) -> Principal:
    if caller.business_id or not caller.uid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Call with sub '-' and the person's uid")
    person = await find_principal(request.app.state, caller.uid)
    if not person:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown or disabled person")
    return person


def requires_staff(app_scope: Scope, person_scope: Scope | None = None):
    async def check(request: Request, caller: Caller = Depends(verify)) -> Principal:
        if str(app_scope) not in caller.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {app_scope}")
        person = await principal(request, caller)
        if person_scope and not person.has(person_scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {person_scope}")
        return person

    return Depends(check)


def need(person: Principal, scope: Scope, app_id: str | None = None) -> None:
    """Scope, and membership of the app when one is named (its staging twin is the same app)."""
    if not person.may(scope, staged(app_id)[0] if app_id else None):
        raise PlatformError(f"Requires {scope}" + (f" on {app_id}" if app_id else ""), 403)
