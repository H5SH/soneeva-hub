"""/v1/roles: each role's console ids and `payment.*`/`platform.*` scopes, in one list."""

from fastapi import APIRouter

from src.dependencies import RolesDep
from src.models.admin import Scope
from src.models.people import Actor, RoleCreate, RolePut
from src.services.people.access import requires_actor

router = APIRouter(prefix="/v1/roles", tags=["people"])
VIEW, UPDATE = Scope.HUB_PEOPLE_VIEW, Scope.HUB_PEOPLE_UPDATE


@router.get("")
async def list_roles(roles: RolesDep, actor: Actor = requires_actor(VIEW)) -> dict:
    """`roles` with their holders, `scopes` (the catalogue a role picks from), `assignable` (the
    roles the caller may give)."""
    return await roles.overview(actor)


@router.post("", status_code=201)
async def create_role(body: RoleCreate, roles: RolesDep, actor: Actor = requires_actor(UPDATE)) -> dict:
    return await roles.create(actor, body.name, body.scopes)


@router.put("/{name}")
async def save_role(name: str, body: RolePut, roles: RolesDep, actor: Actor = requires_actor(UPDATE)) -> dict:
    return await roles.update(actor, name, body.scopes)


@router.delete("/{name}")
async def delete_role(name: str, roles: RolesDep, actor: Actor = requires_actor(UPDATE)) -> dict:
    """409 while anyone holds it."""
    return await roles.delete(actor, name)
