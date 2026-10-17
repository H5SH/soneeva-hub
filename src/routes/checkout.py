from fastapi import APIRouter, Depends

from src.dependencies import AppDep, AuthDep, BillingDep
from src.models.api import CheckoutRequest, PortalRequest, UrlResponse
from src.services.tokens.deps import require_api_token

router = APIRouter(prefix="/v1/apps/{app_id}", tags=["checkout"], dependencies=[Depends(require_api_token)])


@router.post("/checkout/session", response_model=UrlResponse)
async def create_checkout_session(
    app: AppDep, auth: AuthDep, billing: BillingDep, body: CheckoutRequest
) -> UrlResponse:
    """Returns the Stripe Checkout URL. Stripe returns straight to the app; webhooks sync state."""
    success_url = app.resolve_return_url(body.success_url)
    cancel_url = app.resolve_return_url(body.cancel_url or body.success_url)
    session = await billing.start_checkout(app, auth, body, success_url, cancel_url)
    return UrlResponse(url=session.url)


@router.post("/billing/portal", response_model=UrlResponse)
async def create_portal_session(
    app: AppDep, auth: AuthDep, billing: BillingDep, body: PortalRequest
) -> UrlResponse:
    """Card updates, cancellations and receipts, handled by the gateway's own portal."""
    url = await billing.portal_url(auth, app.resolve_return_url(body.return_url))
    return UrlResponse(url=url)
