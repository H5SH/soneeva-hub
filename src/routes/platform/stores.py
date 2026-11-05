"""/v1/stores and a store's side of /v1/businesses/{id}: the console, for its Shopify and BigCommerce apps."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path

from src.dependencies import BusinessDep, StoresDep, VoiceDep
from src.models.admin import Scope
from src.models.identity import Identity
from src.models.stores import STORE_ID, StoreEvent, StorePlan, StorePlatform, StoreRef
from src.services.errors import PlatformError
from src.services.people.access import requires_actor
from src.services.tokens.caller import requires_caller

router = APIRouter(prefix="/v1", tags=["stores"])


def _store(platform: StorePlatform, store_id: Annotated[str, Path(pattern=STORE_ID)]) -> StoreRef:
    return StoreRef(platform=platform, storeId=store_id)


StoreDep = Annotated[StoreRef, Depends(_store)]


@router.post("/stores/sign-in")
async def sign_in(
    body: StoreRef, stores: StoresDep, businesses: BusinessDep, tasks: BackgroundTasks,
    caller: Identity = requires_caller(Scope.HUB_STORE_SIGN_IN),
) -> dict:
    """Sub `-`: {businessId, status (`active` or `disabled`), provisioned, owner}. 404 when no business has
    the store. A business still not provisioned (say its sign-up tab was closed on a failure) is retried."""
    if caller.business_id:
        raise PlatformError("Store sign-in is called with sub '-'", 403)
    found = await stores.sign_in(caller, body)
    if not found["provisioned"]:
        tasks.add_task(businesses.provisioner.quietly, found["businessId"])
    return found


@router.put("/stores/{platform}/{store_id}", status_code=204)
async def connect(
    store: StoreDep, stores: StoresDep, actor: Identity = requires_actor(Scope.HUB_STORE_UPDATE),
) -> None:
    """The business's owner. 409 when another business has the store."""
    await stores.connect(actor, store)


@router.delete("/stores/{platform}/{store_id}", status_code=204)
async def disconnect(
    store: StoreDep, stores: StoresDep, actor: Identity = requires_actor(Scope.HUB_STORE_UPDATE),
) -> None:
    """The business's owner."""
    await stores.disconnect(actor, store)


@router.post("/businesses/{business_id}/disable")
async def disable(
    business_id: str, stores: StoresDep, body: StoreEvent | None = None,
    caller: Identity = requires_caller(Scope.HUB_STORE_UPDATE),
) -> dict:
    """Its store uninstalled the app: a soft delete that a reinstall undoes. `at`, the platform's time for
    it, lets a retry that arrives after a reinstall be ignored."""
    at = body.at if body else None
    return (await stores.disable(caller, business_id, at)).model_dump(mode="json")


@router.post("/businesses/{business_id}/enable")
async def enable(
    business_id: str, stores: StoresDep, caller: Identity = requires_caller(Scope.HUB_STORE_UPDATE),
) -> dict:
    """Its store installed the app again."""
    return (await stores.enable(caller, business_id)).model_dump(mode="json")


@router.put("/businesses/{business_id}/plan")
async def set_plan(
    business_id: str, body: StorePlan, stores: StoresDep, businesses: BusinessDep, voice: VoiceDep,
    caller: Identity = requires_caller(Scope.HUB_STORE_UPDATE),
) -> dict:
    """A plan paid through Shopify. Going active does what a completed Stripe checkout does: the voice env."""
    business = await stores.set_plan(caller, business_id, body)
    if business.storePlan and business.storePlan.is_active:
        await voice.provide(business_id)
    return (await businesses.with_plan(business)).model_dump(mode="json")
