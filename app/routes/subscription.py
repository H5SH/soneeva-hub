from fastapi import APIRouter, Query

from app.dependencies import AppDep, AuthDep, BillingDep
from app.models.api import (
    CancelRequest,
    ChangePlanRequest,
    EntitlementsResponse,
    InvoiceListResponse,
    PlanResponse,
    SubscriptionResponse,
)
from app.models.payment import PaymentMethod

router = APIRouter(prefix="/v1/apps/{app_id}", tags=["subscription"])


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(app: AppDep, auth: AuthDep, billing: BillingDep) -> SubscriptionResponse:
    """Full billing state for the calling store."""
    record = await billing.get_record(app.app_id, auth.store_hash)
    return billing.to_response(app, record)


@router.get("/entitlements", response_model=EntitlementsResponse)
async def get_entitlements(app: AppDep, auth: AuthDep, billing: BillingDep) -> EntitlementsResponse:
    """Small, cacheable answer to 'what is this store allowed to do right now'."""
    record = await billing.get_record(app.app_id, auth.store_hash)
    return billing.to_entitlements(app, record)


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(app: AppDep, auth: AuthDep, billing: BillingDep) -> list[PlanResponse]:
    record = await billing.get_record(app.app_id, auth.store_hash)
    return await billing.list_plans(app, record)


@router.post("/subscription/change", response_model=SubscriptionResponse)
async def change_plan(
    app: AppDep, auth: AuthDep, billing: BillingDep, body: ChangePlanRequest
) -> SubscriptionResponse:
    """Upgrade or downgrade in place, without sending the merchant back to checkout."""
    record = await billing.change_plan(app, auth, body.plan_code, body.proration_behavior)
    return billing.to_response(app, record)


@router.post("/subscription/cancel", response_model=SubscriptionResponse)
async def cancel_subscription(
    app: AppDep, auth: AuthDep, billing: BillingDep, body: CancelRequest
) -> SubscriptionResponse:
    record = await billing.cancel(app, auth, body.at_period_end)
    return billing.to_response(app, record)


@router.post("/subscription/resume", response_model=SubscriptionResponse)
async def resume_subscription(app: AppDep, auth: AuthDep, billing: BillingDep) -> SubscriptionResponse:
    """Undo a pending end-of-period cancellation."""
    record = await billing.resume(app, auth)
    return billing.to_response(app, record)


@router.post("/subscription/sync", response_model=SubscriptionResponse)
async def sync_subscription(app: AppDep, auth: AuthDep, billing: BillingDep) -> SubscriptionResponse:
    """Force a re-read from the gateway. Use when a webhook was missed."""
    record = await billing.sync(app, auth)
    return billing.to_response(app, record)


@router.get("/invoices", response_model=InvoiceListResponse)
async def list_invoices(
    auth: AuthDep,
    billing: BillingDep,
    limit: int = Query(default=20, ge=1, le=100),
    starting_after: str | None = Query(default=None, description="Invoice id to page after"),
) -> InvoiceListResponse:
    invoices, has_more = await billing.list_invoices(auth, limit, starting_after)
    return InvoiceListResponse(data=invoices, has_more=has_more)


@router.get("/payment/method", response_model=PaymentMethod | None)
async def get_payment_method(auth: AuthDep, billing: BillingDep) -> PaymentMethod | None:
    """Card on file, for a 'Visa ···· 4242' line in the app's settings screen."""
    return await billing.payment_method(auth)
