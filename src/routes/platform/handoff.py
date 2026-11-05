from fastapi import APIRouter

from src.dependencies import HandoffDep
from src.models.admin import Scope
from src.models.identity import Identity
from src.models.staff import HandoffRedeem, HandoffRequest
from src.services.people.access import requires_actor, requires_app

router = APIRouter(prefix="/v1/handoff", tags=["handoff"])


@router.post("")
async def handoff(
    body: HandoffRequest, handoff: HandoffDep, actor: Identity = requires_actor(Scope.HUB_HANDOFF_CREATE)
) -> dict:
    """Sub `-`, uid = the serviceperson: a one-time console link (60 s) for a business they hold a
    running grant in."""
    return await handoff.create(actor, body.businessId)


@router.post("/redeem")
async def redeem(
    body: HandoffRedeem, handoff: HandoffDep, caller: Identity = requires_app(Scope.HUB_HANDOFF_REDEEM)
) -> dict:
    """The console only, sub `-` (or the code's business): the serviceperson and their grant."""
    return await handoff.redeem(caller, body.code)
