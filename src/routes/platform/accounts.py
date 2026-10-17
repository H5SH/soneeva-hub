"""/v1/accounts: the console's own business members and each person's own account."""

from fastapi import APIRouter, BackgroundTasks

from src.dependencies import MembersDep
from src.models.admin import Scope
from src.models.people import AccountCreate, AccountPatch, Actor, RecoverRequest
from src.models.platform import Caller
from src.services.people.access import requires_actor, requires_app

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.post("/password-reset-request", status_code=202)
async def password_reset_request(
    body: RecoverRequest, members: MembersDep, tasks: BackgroundTasks,
    caller: Caller = requires_app(Scope.HUB_ACCOUNT_RECOVER),
) -> dict:
    """Always 202, whether or not the address has an account; the email goes out afterwards."""
    tasks.add_task(members.recover, body.email)
    return {"accepted": True}


@router.get("/{uid}")
async def get_account(
    uid: str, members: MembersDep, actor: Actor = requires_actor(Scope.HUB_ACCOUNT_VIEW)
) -> dict:
    return (await members.get(actor, uid)).model_dump()


@router.post("", status_code=201)
async def create_account(
    body: AccountCreate, members: MembersDep, actor: Actor = requires_actor(Scope.HUB_ACCOUNT_CREATE)
) -> dict:
    return (await members.create(actor, body)).model_dump()


@router.patch("/{uid}")
async def update_account(
    uid: str, body: AccountPatch, members: MembersDep, actor: Actor = requires_actor(Scope.HUB_ACCOUNT_UPDATE)
) -> dict:
    return {"account": (await members.update(actor, uid, body)).model_dump()}


@router.delete("/{uid}")
async def delete_account(
    uid: str, members: MembersDep, actor: Actor = requires_actor(Scope.HUB_ACCOUNT_DELETE)
) -> dict:
    await members.delete(actor, uid)
    return {"deleted": True}
