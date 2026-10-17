"""Billing rules over Stripe. Routes only translate HTTP to these calls.

A tenant is the Stripe customer with metadata {app, tenant}. Its id never changes, so a found id
is kept for the life of the process; the subscription is re-read after `stripe.tenantCacheSeconds`
or as soon as a webhook or our own write names the tenant (`forget`).
"""

from fastapi import HTTPException, status

from src.models.api import CheckoutRequest, EntitlementsResponse, PlanResponse, SubscriptionResponse
from src.models.app import AppConfig, Plan
from src.models.auth import AuthContext
from src.models.payment import CheckoutSession, Invoice, PaymentMethod
from src.models.subscription import GatewaySubscription, SubscriptionRecord, SubscriptionStatus
from src.payment import PaymentGateway
from src.payment.stripe.gateway import newest
from src.services.ttl import MISSING, Ttl
from src.tunables import tunable

GRACE_STATUSES = (SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID)
ENTITLEMENT_FIELDS = {"app_id", "tenant", "status", "current_period_end", "cancel_at_period_end"}


def record_of(
    app_id: str, tenant: str, gateway: GatewaySubscription | None, customer_id: str | None = None
) -> SubscriptionRecord:
    fields = gateway.model_dump(exclude={"metadata", "created"}) if gateway else {"customer_id": customer_id}
    return SubscriptionRecord(**fields, app_id=app_id, tenant=tenant)


class BillingService:
    def __init__(self, payment: PaymentGateway) -> None:
        self._payment = payment
        self._customers: dict[tuple[str, str], str] = {}
        self._records = Ttl(tunable("stripe.tenantCacheSeconds"))

    def forget(self, app_id: str, tenant: str) -> None:
        self._records.pop((app_id, tenant))

    async def customer_id(self, app_id: str, tenant: str, create: bool = False) -> str | None:
        key = (app_id, tenant)
        if key not in self._customers:
            found = await self._payment.find_customer(app_id, tenant)
            if not found and create:
                found = await self._payment.create_customer(app_id, tenant, tenant)
            if not found:
                return None
            self._customers[key] = found
        return self._customers[key]

    async def get_record(self, app_id: str, tenant: str) -> SubscriptionRecord:
        record = self._records.get((app_id, tenant))
        if record is MISSING:
            customer = await self.customer_id(app_id, tenant)
            gateway = await self._payment.customer_subscription(customer) if customer else None
            record = self._keep(record_of(app_id, tenant, gateway, customer))
        return record

    async def records(self, app_id: str) -> dict[str, SubscriptionRecord]:
        """Every tenant's newest subscription, for listings (eventually consistent)."""
        by_tenant: dict[str, list[GatewaySubscription]] = {}
        for sub in await self._payment.app_subscriptions(app_id):
            by_tenant.setdefault(sub.metadata.get("tenant", ""), []).append(sub)
        return {t: record_of(app_id, t, newest(subs)) for t, subs in by_tenant.items() if t}

    async def sync(self, app_id: str, tenant: str) -> SubscriptionRecord:
        """Re-read Stripe now instead of waiting for the cache."""
        self.forget(app_id, tenant)
        return await self.get_record(app_id, tenant)

    def _keep(self, record: SubscriptionRecord) -> SubscriptionRecord:
        return self._records.put((record.app_id, record.tenant), record)

    def to_response(self, app: AppConfig, record: SubscriptionRecord) -> SubscriptionResponse:
        plan = app.plan(record.plan_code)
        return SubscriptionResponse(**record.model_dump(), active=record.is_active,
                                    plan_name=plan.name if plan else None)

    def to_entitlements(self, app: AppConfig, record: SubscriptionRecord) -> EntitlementsResponse:
        plan = app.plan(record.plan_code) if record.is_active else app.free_plan
        return EntitlementsResponse(
            **record.model_dump(include=ENTITLEMENT_FIELDS),
            plan_code=plan.code if plan else None,
            active=record.is_active,
            in_grace_period=record.status in GRACE_STATUSES,
            trialing=record.status is SubscriptionStatus.TRIALING,
            features=plan.features if plan else [],
        )

    def list_plans(self, app: AppConfig, record: SubscriptionRecord) -> list[PlanResponse]:
        """Plans on sale, and the current one even when it no longer is."""
        current = record.plan_code if record.is_active else None
        return [PlanResponse(**p.model_dump(), is_current=p.code == current)
                for p in app.plans if p.active or p.code == current]

    async def start_checkout(
        self, app: AppConfig, auth: AuthContext, request: CheckoutRequest, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        plan = self._require_paid_plan(app, request.plan_code)
        record = await self.get_record(app.id, auth.tenant)
        if record.is_active and record.subscription_id:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Tenant already has an active subscription. Change the plan instead.")
        customer_id = await self.customer_id(app.id, auth.tenant, create=True)
        return await self._payment.create_checkout_session(
            customer_id=str(customer_id), price_id=str(plan.price_id), success_url=success_url,
            cancel_url=cancel_url, metadata=auth.gateway_metadata, quantity=request.quantity,
            trial_days=request.trial_days if request.trial_days is not None else plan.trial_days,
            promo_code=request.promo_code, metered=plan.metered,
        )

    async def portal_url(self, auth: AuthContext, return_url: str) -> str:
        customer_id = await self.customer_id(auth.app_id, auth.tenant)
        if not customer_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No billing account for this tenant yet")
        return await self._payment.create_portal_session(customer_id, return_url)

    async def change_plan(
        self, app: AppConfig, auth: AuthContext, plan_code: str, proration_behavior: str
    ) -> SubscriptionRecord:
        plan = self._require_paid_plan(app, plan_code)
        record = await self._require_subscription(app, auth)
        if record.price_id == plan.price_id and record.is_active:
            return record
        gateway = await self._payment.change_subscription_plan(
            str(record.subscription_id), str(plan.price_id), proration_behavior)
        return self._keep(record_of(app.id, auth.tenant, gateway))

    async def cancel(self, app: AppConfig, auth: AuthContext, at_period_end: bool) -> SubscriptionRecord:
        record = await self._require_subscription(app, auth)
        gateway = await self._payment.cancel_subscription(str(record.subscription_id), at_period_end)
        return self._keep(record_of(app.id, auth.tenant, gateway))

    async def resume(self, app: AppConfig, auth: AuthContext) -> SubscriptionRecord:
        record = await self._require_subscription(app, auth)
        if not record.cancel_at_period_end:
            return record
        gateway = await self._payment.resume_subscription(str(record.subscription_id))
        return self._keep(record_of(app.id, auth.tenant, gateway))

    async def list_invoices(
        self, auth: AuthContext, limit: int, starting_after: str | None
    ) -> tuple[list[Invoice], bool]:
        customer_id = await self.customer_id(auth.app_id, auth.tenant)
        if not customer_id:
            return [], False
        return await self._payment.list_invoices(customer_id, limit, starting_after)

    async def payment_method(self, auth: AuthContext) -> PaymentMethod | None:
        customer_id = await self.customer_id(auth.app_id, auth.tenant)
        return await self._payment.get_default_payment_method(customer_id) if customer_id else None

    def _require_paid_plan(self, app: AppConfig, plan_code: str) -> Plan:
        plan = app.plan(plan_code)
        if not plan:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown plan '{plan_code}'")
        if not plan.price_id or not plan.active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Plan '{plan_code}' is not purchasable")
        return plan

    async def _require_subscription(self, app: AppConfig, auth: AuthContext) -> SubscriptionRecord:
        record = await self.sync(app.id, auth.tenant)
        if not record.subscription_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No subscription for this tenant")
        return record
