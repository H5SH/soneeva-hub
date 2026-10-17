"""/v1 callers: `Authorization: Bearer <EdDSA JWT>` with aud karbon-hub.

Effective scopes are the app's `hub.*` grants, narrowed to the person's role when the token
names one (`uid`, with sub `-`). A business subject only ever reaches its own business.
"""

from fastapi import Depends, HTTPException, Request, status

from src.models.admin import Scope, UserRecord
from src.models.auth import Audience
from src.models.db import Default
from src.models.mode import STAGING_ONLY, Mode, staged
from src.models.platform import Caller
from src.services.people.rules import business_reach, holds
from src.services.tokens.verify import AuthError

#: The platform scopes (any one) a person's role needs for each app scope; `*` covers them all.
#: A role never needs a `hub.*` id: those are what the calling app may do.
PERSON_SCOPES: dict[Scope, tuple[str, ...]] = {
    Scope.HUB_BUSINESS_CREATE: ("Businesses.Write",),
    Scope.HUB_BUSINESS_VIEW: ("Businesses.View", "Businesses.Access", "Businesses.Assign"),
    # Businesses.Access only reaches POST …/checklist/meeting; PATCH re-checks Write/Assign/Delete per field.
    Scope.HUB_BUSINESS_UPDATE: (
        "Businesses.Write", "Businesses.Assign", "Businesses.Access", "Businesses.Delete",
    ),
    Scope.HUB_SERVICEPERSON_VIEW: ("Servicepersons.View", "Businesses.Assign"),
    Scope.HUB_VOICE_UPDATE: ("Businesses.Write",),
    Scope.HUB_NUMBER_VIEW: ("Businesses.Write",),
    Scope.HUB_NUMBER_BUY: ("Businesses.Write",),
    Scope.HUB_ADVERTISEMENT_VIEW: ("Advertise.View", "Advertise.Write"),
    Scope.HUB_ADVERTISEMENT_UPDATE: ("Advertise.Write",),
}


def person_allows(role: list[str], scope: str) -> bool:
    return any(holds(role, p) for p in PERSON_SCOPES.get(scope, ()))  # type: ignore[call-overload]


async def verify(request: Request) -> Caller:
    """The token alone: the app's own scopes, not yet narrowed to any person."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    state = request.app.state
    try:
        claims = await state.verifier.verify(token, Audience.HUB)
    except AuthError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error

    credentials = await state.keys.get(claims.iss)
    app_id, mode = staged(claims.iss)
    caller = Caller(app_id=app_id, mode=mode, sub=claims.sub, uid=claims.uid,
                    scopes=sorted(credentials.scopes if credentials else []))
    if mode is Mode.TEST and caller.business_id:
        row = await state.db.get_row(Default.BUSINESSES, caller.business_id)
        if row and not row.get("test"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, STAGING_ONLY)
    return caller


async def _with_person(request: Request, caller: Caller) -> Caller:
    """The named person's role, business limit and business reach; 403 when unknown or disabled."""
    state = request.app.state
    row = await state.db.get_row(Default.USERS, caller.uid)
    person = UserRecord(**(row | {"uid": caller.uid})) if row else None
    if not person or person.disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown or disabled person")
    role = await state.roles.raw(person.role)
    caller.businesses, caller.person_scopes = person.businesses, role
    caller.assigned_only = business_reach(role) == "assigned"
    return caller


async def authenticate(request: Request) -> Caller:
    caller = await verify(request)
    if caller.uid and caller.sub == "-":
        await _with_person(request, caller)
        caller.scopes = [s for s in caller.scopes if person_allows(caller.person_scopes or [], s)]
    return caller


async def person(request: Request) -> Caller:
    """A platform person (sub `-` and their uid); the app's scopes stay unnarrowed, the route checks each."""
    caller = await verify(request)
    if caller.business_id or not caller.uid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Call with sub '-' and the person's uid")
    return await _with_person(request, caller)


def requires_caller(scope: Scope):
    async def check(caller: Caller = Depends(authenticate)) -> Caller:
        if str(scope) not in caller.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {scope}")
        return caller

    return Depends(check)
