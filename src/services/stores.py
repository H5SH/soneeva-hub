"""Which Shopify or BigCommerce store belongs to which business: `(default)/stores/{platform}:{storeId}`.

The store's details and token are the business's own, in its database, written by the console. The console
checks what the platform sent before it names a store here. An uninstall disables the business (soft delete,
status `deleted` with `uninstalled`) and keeps its store, so a reinstall finds it and gives its status back.
A retention job outside this repo counts 30 days from `uninstalled.at`, within `shop/redact`'s limit.
Shopify ends a subscription when its app is uninstalled, so a Shopify plan ends too; a reinstall charges anew.
A Stripe subscription isn't cancelled: per-minute costs nothing once the voice stops.
"""

from datetime import UTC, datetime
from typing import Any

from src.auth import AccountProvider
from src.db import Database
from src.models.db import BusinessDb, Default
from src.models.identity import Identity
from src.models.mode import Mode
from src.models.platform import Business, BusinessStatus
from src.models.stores import StorePlan, StorePlanStatus, StoreRef
from src.services.audit import audit
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.modes import ByMode
from src.services.plans import PlanService
from src.services.store_rows import claim_store


class StoreService:
    def __init__(self, db: Database, businesses: BusinessService, accounts: AccountProvider,
                 plans: ByMode[PlanService]) -> None:
        self._db, self._businesses, self._accounts, self._plans = db, businesses, accounts, plans

    async def _stores(self, business_id: str) -> list[dict[str, Any]]:
        return await self._db.get_rows(Default.STORES, {"businessId": business_id})

    async def _owned(self, actor: Identity) -> Business:
        """The business of its owner. Only they connect a store: whoever opens the app from a connected
        store is signed in as the owner."""
        if not actor.business_id:
            raise PlatformError("Call with sub = businessId", 403)
        business = await self._businesses.require(None, actor.business_id)
        if actor.uid != business.ownerUid:
            raise PlatformError("Only the business's owner connects its store", 403)
        return business

    async def sign_in(self, caller: Identity, store: StoreRef) -> dict[str, Any]:
        """The store's business and owner. `disabled`: nobody signs in until `enable` lets them, which it
        does only for a reinstall, not for a business deleted on the platform or a disabled owner. An
        account that is gone answers too, with no owner, so an uninstall still reaches `disable`."""
        row = await self._db.get_row(Default.STORES, store.doc(caller.mode is Mode.TEST))
        if not row:
            raise PlatformError("No business has this store", 404)
        business = await self._businesses.require(caller, row["businessId"])
        owner = await self._accounts.get(business.ownerUid)
        off = not owner or owner.disabled or business.status is BusinessStatus.DELETED
        return {"businessId": business.id, "status": "disabled" if off else "active",
                "provisioned": business.provisioned, "owner": owner.model_dump() if owner else None}

    async def disable(self, caller: Identity, business_id: str, at: datetime | None = None) -> Business:
        """The store uninstalled the app at `at`. An event from before the last reinstall is a late retry,
        and changes nothing; so does a repeat. A business already deleted on the platform stays deleted,
        with its plan ended and its date recorded."""
        business = await self._businesses.require(caller, business_id)
        if not await self._stores(business_id):
            raise PlatformError("This business has no store", 409)
        late = at and business.reenabledAt and at <= business.reenabledAt
        if late or business.uninstalled:
            return business
        now = datetime.now(UTC)
        deleted = business.status is BusinessStatus.DELETED
        fields: dict[str, Any] = {
            "status": BusinessStatus.DELETED.value,
            "uninstalled": {"status": None if deleted else business.status.value, "at": now}}
        if business.storePlan:
            ended = business.storePlan.model_copy(update={"status": StorePlanStatus.CANCELLED, "at": now})
            fields["storePlan"] = ended.model_dump(mode="json")
            await self._businesses.checklist.on_subscription(business_id, False)
        await self._businesses.set_fields(business_id, fields)
        await audit(self._db, "business.disable", business_id, caller.app_id, {})
        return await self._businesses.require(caller, business_id)

    async def enable(self, caller: Identity, business_id: str) -> Business:
        """The store installed the app again. Safe to repeat; never undoes a delete made on the platform."""
        business = await self._businesses.require(caller, business_id)
        owner = await self._accounts.get(business.ownerUid)
        if not owner or owner.disabled:
            raise PlatformError("The owner's account is gone or disabled", 409)
        if business.status is not BusinessStatus.DELETED:
            return business
        if not business.uninstalled or not business.uninstalled.status:
            raise PlatformError("This business was deleted, not uninstalled", 409)
        await self._businesses.set_fields(business_id, {
            "status": business.uninstalled.status.value, "uninstalled": None,
            "reenabledAt": datetime.now(UTC)})
        await audit(self._db, "business.enable", business_id, caller.app_id, {})
        return await self._businesses.require(caller, business_id)

    async def connect(self, actor: Identity, store: StoreRef) -> None:
        """A business has one store per platform: its token lives at `connections/{platform}`."""
        business = await self._owned(actor)
        mine = await self._stores(business.id)
        if any(r["platform"] == store.platform and r["storeId"] != store.storeId for r in mine):
            raise PlatformError(f"This business already has a {store.platform} store", 409)
        if not await claim_store(self._db, store, business.id, business.test):
            raise PlatformError("This store belongs to another business", 409)
        await audit(self._db, "store.connect", business.id, actor.uid, {"store": store.storeId})

    async def disconnect(self, actor: Identity, store: StoreRef) -> None:
        """The store is forgotten; the business keeps running on its own data."""
        business = await self._owned(actor)
        row = await self._db.get_row(Default.STORES, store.doc(business.test))
        if not row or row["businessId"] != business.id:
            raise PlatformError("This business has no such store", 404)
        await self._forget(store, business, actor.uid)

    async def release(self, store: StoreRef, test: bool, by: str) -> str:
        """An operator frees a store stuck on a dead business, so it can sign up again. Returns its id."""
        row = await self._db.get_row(Default.STORES, store.doc(test))
        business = await self._businesses.get(row["businessId"]) if row else None
        if not business:
            raise PlatformError("No business has this store", 404)
        await self._forget(store, business, by)
        return business.id

    async def _forget(self, store: StoreRef, business: Business, by: str) -> None:
        """Its store gone, the business pays through Stripe from now on: a plan paid through it ends."""
        await self._db.delete_row(Default.STORES, store.doc(business.test))
        if business.storePlan and business.storePlan.source == store.platform:
            await self._businesses.set_fields(business.id, {"storePlan": None, "chargeFailedAt": None})
            await self._businesses.checklist.on_subscription(business.id, False)
        await audit(self._db, "store.disconnect", business.id, by, {"store": store.storeId})

    async def set_plan(self, caller: Identity, business_id: str, plan: StorePlan) -> Business:
        """After the merchant approves the charge, and on each change Shopify reports. Old news changes
        nothing: anything older than the current plan, a non-active word about another subscription, or
        `active` while uninstalled. Going active needs a token to charge with, and no Stripe subscription."""
        business = await self._businesses.require(caller, business_id)
        current = business.storePlan
        same = current is not None and current.subscriptionId == plan.subscriptionId
        stale = current is not None and plan.at < current.at
        if stale or (not plan.is_active and not same) or (plan.is_active and business.uninstalled):
            return business
        if not any(r["platform"] == plan.source for r in await self._stores(business_id)):
            raise PlatformError(f"This business has no {plan.source.value} store", 409)
        if not await self._plans[business.mode].get(business.appId, plan.code):
            raise PlatformError(f"Unknown plan '{plan.code}'", 422)
        if same and current and not plan.usageLineItemId:
            plan = plan.model_copy(update={"usageLineItemId": current.usageLineItemId})
        if plan.is_active:
            await self._may_go_active(business, plan)
        stored = plan.model_dump(mode="json")
        fields = {"storePlan": stored} | ({"chargeFailedAt": None} if plan.is_active else {})
        await self._businesses.set_fields(business_id, fields)
        await self._businesses.checklist.on_subscription(business_id, plan.is_active)
        await audit(self._db, "business.plan", business_id, caller.app_id, stored)
        return await self._businesses.require(caller, business_id)

    async def _may_go_active(self, business: Business, plan: StorePlan) -> None:
        if not plan.usageLineItemId:
            raise PlatformError("An active plan needs usageLineItemId", 422)
        token = await self._businesses.business_db(business).get_row(BusinessDb.CONNECTIONS, plan.source)
        if not token:
            raise PlatformError("The business has no store token to charge with", 409)
        if not business.storePlan and (await self._businesses.record(business)).is_active:
            raise PlatformError("This business already pays through Stripe", 409)
