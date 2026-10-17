from fastapi import APIRouter, Depends, Query

from src.dependencies import AppDep, AuthDep, BillingDep
from src.models.api import (
    CancelRequest,
    ChangePlanRequest,
    EntitlementsResponse,
    InvoiceListResponse,
    PlanResponse,
    SubscriptionResponse,
)
from src.models.payment import PaymentMethod
from src.services.tokens.deps import require_api_token

router = APIRouter(
    prefix="/v1/apps/{app_id}", tags=["subscription"], dependencies=[Depends(require_api_token)]
)


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(app: AppDep, auth: AuthDep, billing: BillingDep) -> SubscriptionResponse:
    """Full billing state for the calling tenant."""
    record = await billing.get_record(app.id, auth.tenant)
    return billing.to_response(app, record)


@router.get("/entitlements", response_model=EntitlementsResponse)
async def get_entitlements(app: AppDep, auth: AuthDep, billing: BillingDep) -> EntitlementsResponse:
    """Small, cacheable answer to 'what is this tenant allowed to do right now'."""
    record = await billing.get_record(app.id, auth.tenant)
    return billing.to_entitlements(app, record)


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(app: AppDep, auth: AuthDep, billing: BillingDep) -> list[PlanResponse]:
    return billing.list_plans(app, await billing.get_record(app.id, auth.tenant))


@router.post("/subscription/change", response_model=SubscriptionResponse)
async def change_plan(
    app: AppDep, auth: AuthDep, billing: BillingDep, body: ChangePlanRequest
) -> SubscriptionResponse:
    """Upgrade or downgrade in place, without sending the customer back to checkout."""
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
    """Re-read Stripe now instead of after the short cache."""
    record = await billing.sync(app.id, auth.tenant)
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
