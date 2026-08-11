import asyncio

from fastapi import HTTPException, status

from app.models.api import (
    CheckoutRequest,
    EntitlementsResponse,
    PlanResponse,
    SubscriptionResponse,
)
from app.models.app import AppConfig, PlanConfig
from app.models.auth import AuthContext
from app.models.db import Collection
from app.models.payment import CheckoutSession, Invoice, PaymentMethod
from app.models.subscription import (
    GatewaySubscription,
    SubscriptionRecord,
    SubscriptionStatus,
)
from app.services.db import Database
from app.services.payment import PaymentGateway

GRACE_STATUSES = (SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID)
ENTITLEMENT_FIELDS = {"app_id", "store_hash", "status", "current_period_end", "cancel_at_period_end"}


class BillingService:
    """All billing rules live here. Routes only translate HTTP to these calls."""

    def __init__(self, db: Database, payment: PaymentGateway) -> None:
        self._db = db
        self._payment = payment

    async def get_customer_id(self, auth: AuthContext, create: bool = True) -> str | None:
        row = await self._db.get_row(Collection.CUSTOMERS, auth.key)
        if row and row.get("customer_id"):
            return row["customer_id"]
        customer_id = await self._payment.find_customer(auth.app_id, auth.store_hash)
        if not customer_id:
            if not create:
                return None
            customer_id = await self._payment.create_customer(
                metadata=auth.gateway_metadata, email=auth.email, name=auth.store_hash
            )
        await self._db.set_row(
            Collection.CUSTOMERS,
            auth.key,
            {"app_id": auth.app_id, "store_hash": auth.store_hash, "customer_id": customer_id},
        )
        return customer_id

    async def get_record(self, app_id: str, store_hash: str) -> SubscriptionRecord:
        row = await self._db.get_row(Collection.SUBSCRIPTIONS, f"{app_id}__{store_hash}")
        return SubscriptionRecord(**row) if row else SubscriptionRecord(app_id=app_id, store_hash=store_hash)

    async def save_subscription(
        self, app: AppConfig, store_hash: str, gateway: GatewaySubscription
    ) -> SubscriptionRecord:
        plan = app.plan_by_price(gateway.price_id) if gateway.price_id else None
        record = SubscriptionRecord(
            **gateway.model_dump(exclude={"metadata"}),
            app_id=app.app_id,
            store_hash=store_hash,
            plan_code=plan.code if plan else gateway.metadata.get("plan_code"),
        )
        await self._db.set_row(Collection.SUBSCRIPTIONS, record.key, record.model_dump(mode="json"))
        return record

    async def sync(self, app: AppConfig, auth: AuthContext) -> SubscriptionRecord:
        """Re-read the gateway and overwrite local state. Recovers from a missed webhook."""
        customer_id = await self.get_customer_id(auth, create=False)
        if not customer_id:
            return SubscriptionRecord(app_id=app.app_id, store_hash=auth.store_hash)
        gateway = await self._payment.find_subscription(customer_id)
        if gateway:
            return await self.save_subscription(app, auth.store_hash, gateway)
        record = SubscriptionRecord(
            app_id=app.app_id, store_hash=auth.store_hash, customer_id=customer_id
        )
        await self._db.set_row(Collection.SUBSCRIPTIONS, record.key, record.model_dump(mode="json"))
        return record

    def to_response(self, app: AppConfig, record: SubscriptionRecord) -> SubscriptionResponse:
        plan = app.plan(record.plan_code) if record.plan_code else None
        return SubscriptionResponse(
            **record.model_dump(), active=record.is_active, plan_name=plan.name if plan else None
        )

    def to_entitlements(self, app: AppConfig, record: SubscriptionRecord) -> EntitlementsResponse:
        plan = app.plan(record.plan_code) if record.plan_code else None
        if not record.is_active:
            plan = app.free_plan
        return EntitlementsResponse(
            **record.model_dump(include=ENTITLEMENT_FIELDS),
            plan_code=plan.code if plan else None,
            active=record.is_active,
            in_grace_period=record.status in GRACE_STATUSES,
            trialing=record.status is SubscriptionStatus.TRIALING,
            features=plan.features if plan else [],
            limits=plan.limits if plan else {},
        )

    async def list_plans(self, app: AppConfig, record: SubscriptionRecord) -> list[PlanResponse]:
        prices = await asyncio.gather(
            *(self._payment.get_price(plan.price_id) for plan in app.paid_plans)
        )
        by_id = {price.price_id: price for price in prices if price}
        plans = []
        for plan in app.plans:
            price = by_id.get(plan.price_id or "")
            merged = (price.model_dump() if price else {}) | plan.model_dump(exclude_none=True)
            is_current = plan.code == record.plan_code and record.is_active
            plans.append(PlanResponse(**merged, is_current=is_current))
        return plans

    async def start_checkout(
        self,
        app: AppConfig,
        auth: AuthContext,
        request: CheckoutRequest,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        plan = self._require_paid_plan(app, request.plan_code)
        record = await self.get_record(app.app_id, auth.store_hash)
        if record.is_active and record.subscription_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Store already has an active subscription. Change the plan instead.",
            )
        customer_id = await self.get_customer_id(auth)
        return await self._payment.create_checkout_session(
            customer_id=customer_id,
            price_id=plan.price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=auth.gateway_metadata | {"plan_code": plan.code},
            quantity=request.quantity,
            trial_days=request.trial_days if request.trial_days is not None else plan.trial_days,
            promo_code=request.promo_code,
        )

    async def complete_checkout(self, app: AppConfig, session_id: str) -> SubscriptionRecord | None:
        """Runs on the way back from the gateway, so state is fresh before the app reloads."""
        session = await self._payment.get_checkout_session(session_id)
        store_hash = session.metadata.get("store_hash") if session else None
        if not session or not session.subscription_id or not store_hash:
            return None
        gateway = await self._payment.get_subscription(session.subscription_id)
        return await self.save_subscription(app, store_hash, gateway) if gateway else None

    async def portal_url(self, auth: AuthContext, return_url: str) -> str:
        customer_id = await self.get_customer_id(auth, create=False)
        if not customer_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No billing account for this store yet")
        return await self._payment.create_portal_session(customer_id, return_url)

    async def change_plan(
        self, app: AppConfig, auth: AuthContext, plan_code: str, proration_behavior: str
    ) -> SubscriptionRecord:
        plan = self._require_paid_plan(app, plan_code)
        record = await self._require_subscription(app, auth)
        if record.price_id == plan.price_id and record.is_active:
            return record
        gateway = await self._payment.change_subscription_plan(
            record.subscription_id, plan.price_id, proration_behavior
        )
        return await self.save_subscription(app, auth.store_hash, gateway)

    async def cancel(self, app: AppConfig, auth: AuthContext, at_period_end: bool) -> SubscriptionRecord:
        record = await self._require_subscription(app, auth)
        gateway = await self._payment.cancel_subscription(record.subscription_id, at_period_end)
        return await self.save_subscription(app, auth.store_hash, gateway)

    async def resume(self, app: AppConfig, auth: AuthContext) -> SubscriptionRecord:
        record = await self._require_subscription(app, auth)
        if not record.cancel_at_period_end:
            return record
        gateway = await self._payment.resume_subscription(record.subscription_id)
        return await self.save_subscription(app, auth.store_hash, gateway)

    async def list_invoices(
        self, auth: AuthContext, limit: int, starting_after: str | None
    ) -> tuple[list[Invoice], bool]:
        customer_id = await self.get_customer_id(auth, create=False)
        if not customer_id:
            return [], False
        return await self._payment.list_invoices(customer_id, limit, starting_after)

    async def payment_method(self, auth: AuthContext) -> PaymentMethod | None:
        customer_id = await self.get_customer_id(auth, create=False)
        return await self._payment.get_default_payment_method(customer_id) if customer_id else None

    def _require_paid_plan(self, app: AppConfig, plan_code: str) -> PlanConfig:
        plan = app.plan(plan_code)
        if not plan:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown plan '{plan_code}'")
        if plan.is_free:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Plan '{plan_code}' is not purchasable")
        return plan

    async def _require_subscription(self, app: AppConfig, auth: AuthContext) -> SubscriptionRecord:
        record = await self.get_record(app.app_id, auth.store_hash)
        if not record.subscription_id:
            record = await self.sync(app, auth)
        if not record.subscription_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No subscription for this store")
        return record
