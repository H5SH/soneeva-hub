"""/v1/advertisements: karbon-admin's advertisements (a person holding Advertise.View / Advertise.Write)."""

from fastapi import APIRouter

from src.dependencies import AdvertisementDep
from src.models.admin import Scope
from src.models.advertisement import AdFields, AdPatch, AdPublish
from src.models.platform import Caller
from src.services.advertisements import view
from src.services.errors import PlatformError
from src.services.tokens.caller import requires_caller

router = APIRouter(prefix="/v1/advertisements", tags=["advertisements"])
VIEW, UPDATE = Scope.HUB_ADVERTISEMENT_VIEW, Scope.HUB_ADVERTISEMENT_UPDATE


def _platform(caller: Caller) -> Caller:
    if caller.business_id:
        raise PlatformError("Advertisements are managed by platform people", 403)
    return caller


@router.get("")
async def list_ads(ads: AdvertisementDep, caller: Caller = requires_caller(VIEW)) -> dict:
    _platform(caller)
    return {"advertisements": await ads.every()}


@router.post("", status_code=201)
async def create_ad(body: AdFields, ads: AdvertisementDep, caller: Caller = requires_caller(UPDATE)) -> dict:
    return await ads.create(body, _platform(caller).uid or caller.app_id)


@router.get("/{ad_id}")
async def get_ad(ad_id: str, ads: AdvertisementDep, caller: Caller = requires_caller(VIEW)) -> dict:
    _platform(caller)
    return view(await ads.require(ad_id))


@router.patch("/{ad_id}")
async def update_ad(ad_id: str, body: AdPatch, ads: AdvertisementDep,
                    caller: Caller = requires_caller(UPDATE)) -> dict:
    """Also every published copy; each business's `dismissed` stays."""
    _platform(caller)
    return await ads.update(ad_id, body)


@router.delete("/{ad_id}")
async def delete_ad(ad_id: str, ads: AdvertisementDep, caller: Caller = requires_caller(UPDATE)) -> dict:
    """Soft: the master and every copy get `isDeleted`."""
    _platform(caller)
    return await ads.delete(ad_id)


@router.post("/{ad_id}/publish")
async def publish_ad(ad_id: str, body: AdPublish, ads: AdvertisementDep,
                     caller: Caller = requires_caller(UPDATE)) -> dict:
    """Writes a copy to each named business; only businesses the caller reaches."""
    return await ads.publish(_platform(caller), ad_id, body.businessIds)


@router.delete("/{ad_id}/publish/{business_id}")
async def withdraw_ad(ad_id: str, business_id: str, ads: AdvertisementDep,
                      caller: Caller = requires_caller(UPDATE)) -> dict:
    return await ads.withdraw(_platform(caller), ad_id, business_id)
