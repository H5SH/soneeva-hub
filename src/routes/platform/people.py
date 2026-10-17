"""/v1/me and /v1/people: platform people, one record each (role, payment apps, business limit, tokens)."""

from fastapi import APIRouter, Query, Request

from src.dependencies import PeopleDep
from src.models.admin import Principal, Scope
from src.models.people import Actor, AssignPut, PersonCreate, PersonPatch, ResetRequest, TokensPut
from src.services.people.access import requires_actor
from src.services.tokens.staff import requires_staff

router = APIRouter(prefix="/v1", tags=["people"])
VIEW, UPDATE = Scope.HUB_PEOPLE_VIEW, Scope.HUB_PEOPLE_UPDATE
EXACT = ("payment.", "hub.")


@router.get("/me")
async def me(request: Request, person: Principal = requires_staff(VIEW)) -> dict:
    """`platformScopes`: console ids (`*` = every one) and `platform.*`; `paymentScopes`: `payment.*`."""
    state = request.app.state
    account = await state.accounts.get(person.uid)
    role = await state.roles.raw(person.role)
    return {
        "uid": person.uid, "email": account.email if account else person.email,
        "displayName": account.displayName if account else "",
        "username": account.username if account else None,
        "role": person.role or None,
        "paymentScopes": [str(s) for s in person.scopes if str(s).startswith("payment.")],
        "platformScopes": [s for s in role if not s.startswith(EXACT)],
        "allApps": person.sees_all, "apps": person.apps, "businesses": person.businesses,
    }


@router.get("/people")
async def list_people(
    people: PeopleDep, pageToken: str | None = Query(default=None, max_length=2000),
    actor: Actor = requires_actor(VIEW),
) -> dict:
    found, nxt = await people.page(actor, pageToken)
    return {"people": [p.model_dump() for p in found], "nextPageToken": nxt}


@router.get("/people/{uid}")
async def get_person(uid: str, people: PeopleDep, actor: Actor = requires_actor(VIEW)) -> dict:
    return (await people.get(actor, uid)).model_dump()


@router.post("/people", status_code=201)
async def create_person(body: PersonCreate, people: PeopleDep, actor: Actor = requires_actor(UPDATE)) -> dict:
    """Without `password`, `link` lets them set their own (shown once)."""
    person, link = await people.create(actor, body)
    return {"person": person.model_dump(), "link": link}


@router.patch("/people/{uid}")
async def update_person(
    uid: str, body: PersonPatch, people: PeopleDep, actor: Actor = requires_actor(UPDATE)
) -> dict:
    """`dropped`: assignments removed because they left a serviceperson role."""
    person, dropped = await people.update(actor, uid, body)
    return {"person": person.model_dump(), "dropped": dropped}


@router.delete("/people/{uid}")
async def delete_person(uid: str, people: PeopleDep, actor: Actor = requires_actor(UPDATE)) -> dict:
    """`refunded`: tokens returned to the actor who gave them."""
    return {"deleted": True, "refunded": await people.delete(actor, uid)}


@router.post("/people/{uid}/password-reset")
async def password_reset(
    uid: str, body: ResetRequest, people: PeopleDep, actor: Actor = requires_actor(UPDATE)
) -> dict:
    return await people.reset(actor, uid, body.send)


@router.put("/people/{uid}/tokens")
async def set_tokens(
    uid: str, body: TokensPut, people: PeopleDep, actor: Actor = requires_actor(UPDATE)
) -> dict:
    return (await people.set_tokens(actor, uid, body.tokens)).model_dump()


@router.put("/people/{uid}/servicepersons")
async def set_servicepersons(
    uid: str, body: AssignPut, people: PeopleDep, actor: Actor = requires_actor(UPDATE)
) -> dict:
    return (await people.assign(actor, uid, body.servicepersons)).model_dump()
