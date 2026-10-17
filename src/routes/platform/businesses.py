from fastapi import APIRouter, BackgroundTasks, Query

from src.dependencies import BusinessDep, IvrDep, NumbersDep, PeopleDep, VoiceDep
from src.models.admin import Scope
from src.models.platform import (
    AGENT_CONNECTION,
    BusinessCreate,
    BusinessPatch,
    Caller,
    ChecklistTick,
    NumberPurchase,
    VoiceSettings,
)
from src.services.errors import PlatformError
from src.services.people.access import business_scopes
from src.services.people.rules import CANNOT_READ, reads_business
from src.services.tokens.caller import requires_caller

router = APIRouter(prefix="/v1", tags=["platform"])
CONTACT = {"uid", "name", "phone", "email"}
CHECKLIST_READ = ("checklist.view", "checklist.edit", "checklist.approve")


@router.post("/businesses", status_code=201)
async def create_business(
    body: BusinessCreate, businesses: BusinessDep, tasks: BackgroundTasks,
    caller: Caller = requires_caller(Scope.HUB_BUSINESS_CREATE),
) -> dict:
    """Answers once the account and the pending business exist; provisioning follows in the
    background. Poll GET /v1/businesses/{id} for `provisioned` (or `provisionError`)."""
    if caller.business_id:
        raise PlatformError("Sign-up is called with sub '-'", 403)
    business = await businesses.create(body, caller.uid or caller.app_id, caller.mode)
    if not business.provisioned:
        tasks.add_task(businesses.provisioner.quietly, business.id)
    return business.model_dump(mode="json")


@router.post("/businesses/{business_id}/provision", status_code=202)
async def provision_business(
    business_id: str, businesses: BusinessDep, tasks: BackgroundTasks,
    caller: Caller = requires_caller(Scope.HUB_BUSINESS_CREATE),
) -> dict:
    """Runs provisioning again in the background, e.g. after `provisionError`; nothing once provisioned."""
    business = await businesses.require(caller, business_id)
    if not business.provisioned:
        await businesses.set_fields(business_id, {"provisionError": None})
        tasks.add_task(businesses.provisioner.quietly, business_id)
    return business.model_copy(update={"provisionError": None}).model_dump(mode="json")


@router.get("/businesses")
async def list_businesses(
    businesses: BusinessDep, status: str | None = Query(default=None),
    assigned: str | None = Query(default=None, pattern="^me$"),
    caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW),
) -> dict:
    found = await businesses.visible(caller, status, assigned == "me")
    return {"businesses": [b.model_dump(mode="json") for b in found]}


@router.get("/businesses/{business_id}")
async def get_business(
    business_id: str, businesses: BusinessDep, caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW)
) -> dict:
    return (await businesses.with_plan(await businesses.require(caller, business_id))).model_dump(mode="json")


@router.get("/businesses/{business_id}/branches")
async def list_branches(
    business_id: str, businesses: BusinessDep, caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW)
) -> dict:
    """A platform person who `reads_business`, or a business token whose person holds `branches.view`."""
    business = await businesses.require(caller, business_id)
    if caller.business_id:
        bdb = businesses.business_db(business)
        held = await business_scopes(bdb, caller.uid) if caller.uid else []
        if not {"branches.view", "branches.edit"} & set(held):
            raise PlatformError("Requires branches.view", 403)
    elif not reads_business(caller, business):
        raise PlatformError(CANNOT_READ, 403)
    return {"branches": await businesses.branches(business)}


@router.get("/businesses/{business_id}/checklist")
async def get_checklist(
    business_id: str, businesses: BusinessDep, caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW)
) -> dict:
    """A platform person who `reads_business`, or a business token whose person holds checklist.view."""
    business = await businesses.require(caller, business_id)
    if caller.business_id:
        held = await business_scopes(businesses.business_db(business), caller.uid) if caller.uid else []
        if not set(CHECKLIST_READ) & set(held):
            raise PlatformError("Requires checklist.view", 403)
    elif not reads_business(caller, business):
        raise PlatformError(CANNOT_READ, 403)
    return {"items": await businesses.checklist.items(business)}


@router.patch("/businesses/{business_id}")
async def patch_business(
    business_id: str, body: BusinessPatch, businesses: BusinessDep, voice: VoiceDep,
    tasks: BackgroundTasks, caller: Caller = requires_caller(Scope.HUB_BUSINESS_UPDATE),
) -> dict:
    """A changed agent connection redeploys this business's voice, if it has one."""
    before = await businesses.require(caller, business_id)
    after = await businesses.with_plan(await businesses.patch(caller, business_id, body))
    changed = any(getattr(before, f) != getattr(after, f) for f in AGENT_CONNECTION)
    redeploying = changed and bool(after.voiceUrl)
    if redeploying:
        tasks.add_task(voice.deploy_quietly, business_id)
    return after.model_dump(mode="json") | {"redeploying": redeploying}


@router.post("/businesses/{business_id}/checklist/meeting")
async def tick_meeting(
    business_id: str, body: ChecklistTick, businesses: BusinessDep,
    caller: Caller = requires_caller(Scope.HUB_BUSINESS_UPDATE),
) -> dict:
    """The assigned serviceperson (or Businesses.Write) records the meeting as held."""
    business = await businesses.require(caller, business_id)
    return await businesses.checklist.tick_meeting(caller, business, body.done)


@router.get("/servicepersons")
async def list_servicepersons(
    people: PeopleDep, businesses: BusinessDep,
    caller: Caller = requires_caller(Scope.HUB_SERVICEPERSON_VIEW),
) -> dict:
    """People whose role is `serviceperson`. A business subject gets only its own serviceperson's
    contact card, or an empty list."""
    if not caller.business_id:
        return {"servicepersons": [p.model_dump() for p in await people.servicepersons()]}
    business = await businesses.require(caller, caller.business_id)
    found = await people.servicepersons(business.servicepersonUid) if business.servicepersonUid else []
    return {"servicepersons": [p.model_dump(include=CONTACT) for p in found if p.active]}


@router.post("/businesses/{business_id}/voice/settings")
async def voice_settings(
    business_id: str, body: VoiceSettings, businesses: BusinessDep, voice: VoiceDep,
    tasks: BackgroundTasks, caller: Caller = requires_caller(Scope.HUB_VOICE_UPDATE),
) -> dict:
    business = await businesses.require(caller, business_id)
    result = await voice.save_settings(business, body)
    if result["restarting"]:
        tasks.add_task(voice.deploy_quietly, business_id)
        result["warning"] = "The voice service restarts; calls in progress are dropped."
    return result


@router.get("/businesses/{business_id}/numbers")
async def search_numbers(
    business_id: str, businesses: BusinessDep, numbers: NumbersDep,
    country: str = Query(min_length=2, max_length=2), contains: str = Query(default="", max_length=10),
    caller: Caller = requires_caller(Scope.HUB_NUMBER_VIEW),
) -> dict:
    business = await businesses.require(caller, business_id)
    found = await numbers.search(business, country, contains)
    return {"numbers": [n.model_dump() for n in found]}


@router.post("/businesses/{business_id}/numbers", status_code=201)
async def buy_number(
    business_id: str, body: NumberPurchase, businesses: BusinessDep, numbers: NumbersDep, ivr: IvrDep,
    voice: VoiceDep, tasks: BackgroundTasks, caller: Caller = requires_caller(Scope.HUB_NUMBER_BUY),
) -> dict:
    """The new number gets a default call menu (straight to the agent), so the voice restarts."""
    business = await businesses.require(caller, business_id)
    number, _ = await numbers.buy(business, body.phoneNumber, caller.uid or caller.app_id)
    await ivr.default_number(business, number.phoneNumber)
    tasks.add_task(voice.deploy_quietly, business_id)
    return number.model_dump()
