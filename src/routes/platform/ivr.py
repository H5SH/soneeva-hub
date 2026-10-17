"""/v1/businesses/{id}/ivr/numbers and /audios: call menus and recorded messages.

A business token acts for its person, who needs the business scope named on each route; a
platform person needs Businesses.View and may only read. Every change restarts that business's voice.
"""

from fastapi import APIRouter, BackgroundTasks, Path

from src.dependencies import BusinessDep, IvrDep, VoiceDep
from src.models.admin import Scope
from src.models.ivr import E164, SLUG, AudioPut, NumberConfig
from src.models.platform import Business, Caller
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.people.access import business_scopes
from src.services.people.rules import CANNOT_READ, reads_business
from src.services.tokens.caller import requires_caller
from src.services.voice import VoiceService

router = APIRouter(prefix="/v1/businesses/{business_id}", tags=["ivr"])
IVR_READ = {"ivr.view", "ivr.edit"}
AUDIO_READ = IVR_READ | {"audio.edit"}
Phone = Path(pattern=E164)
Name = Path(pattern=SLUG)


async def allowed(businesses: BusinessService, caller: Caller, business_id: str,
                  scopes: set[str], write: bool = False) -> Business:
    business = await businesses.require(caller, business_id)
    if caller.business_id:
        bdb = businesses.business_db(business)
        held = await business_scopes(bdb, caller.uid) if caller.uid else []
        if not scopes & set(held):
            raise PlatformError(f"Requires {' or '.join(sorted(scopes))}", 403)
    elif write:
        raise PlatformError("Only the business changes its call menus and recordings", 403)
    elif not reads_business(caller, business):
        raise PlatformError(CANNOT_READ, 403)
    return business


def restart(business: Business, voice: VoiceService, tasks: BackgroundTasks) -> bool:
    if business.voiceUrl:
        tasks.add_task(voice.deploy_quietly, business.id)
    return bool(business.voiceUrl)


@router.get("/ivr/numbers")
async def list_numbers(business_id: str, businesses: BusinessDep, ivr: IvrDep,
                       caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW)) -> dict:
    business = await allowed(businesses, caller, business_id, IVR_READ)
    return {"numbers": await ivr.numbers(business)}


@router.put("/ivr/numbers/{phone}")
async def save_number(
    business_id: str, body: NumberConfig, businesses: BusinessDep, ivr: IvrDep, voice: VoiceDep,
    tasks: BackgroundTasks, phone: str = Phone, caller: Caller = requires_caller(Scope.HUB_VOICE_UPDATE),
) -> dict:
    business = await allowed(businesses, caller, business_id, {"ivr.edit"}, write=True)
    saved = await ivr.save_number(business, phone, body, caller.uid or caller.app_id)
    return saved | {"restarting": restart(business, voice, tasks)}


@router.get("/audios")
async def list_audios(business_id: str, businesses: BusinessDep, ivr: IvrDep,
                      caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW)) -> dict:
    business = await allowed(businesses, caller, business_id, AUDIO_READ)
    return {"audios": await ivr.audios(business)}


@router.get("/audios/{name}")
async def get_audio(business_id: str, businesses: BusinessDep, ivr: IvrDep, name: str = Name,
                    caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW)) -> dict:
    business = await allowed(businesses, caller, business_id, AUDIO_READ)
    return await ivr.audio(business, name)


@router.put("/audios/{name}")
async def save_audio(
    business_id: str, body: AudioPut, businesses: BusinessDep, ivr: IvrDep, voice: VoiceDep,
    tasks: BackgroundTasks, name: str = Name, caller: Caller = requires_caller(Scope.HUB_VOICE_UPDATE),
) -> dict:
    """Archives the active recording as v{n}; the restarted voice synthesises the new text."""
    business = await allowed(businesses, caller, business_id, {"audio.edit"}, write=True)
    saved, changed = await ivr.save_audio(business, name, body, caller.uid or caller.app_id)
    return saved | {"restarting": changed and restart(business, voice, tasks)}


@router.delete("/audios/{name}/versions/{n}")
async def delete_version(
    business_id: str, n: int, businesses: BusinessDep, ivr: IvrDep, name: str = Name,
    caller: Caller = requires_caller(Scope.HUB_VOICE_UPDATE),
) -> dict:
    """An old version is not played by anyone: no restart."""
    business = await allowed(businesses, caller, business_id, {"audio.edit"}, write=True)
    await ivr.delete_version(business, name, n)
    return {"deleted": True, "restarting": False}


@router.delete("/audios/{name}")
async def delete_audio(
    business_id: str, businesses: BusinessDep, ivr: IvrDep, voice: VoiceDep, tasks: BackgroundTasks,
    name: str = Name, caller: Caller = requires_caller(Scope.HUB_VOICE_UPDATE),
) -> dict:
    business = await allowed(businesses, caller, business_id, {"audio.edit"}, write=True)
    await ivr.delete_audio(business, name)
    return {"deleted": True, "restarting": restart(business, voice, tasks)}
