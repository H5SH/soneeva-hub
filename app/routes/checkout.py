from fastapi import APIRouter

from app.dependencies import AppDep, AuthDep, BillingDep, SettingsDep
from app.models.api import CheckoutRequest, PortalRequest, UrlResponse
from app.utils import hosted_urls

router = APIRouter(prefix="/v1/apps/{app_id}", tags=["checkout"])


@router.post("/checkout/session", response_model=UrlResponse)
async def create_checkout_session(
    app: AppDep, auth: AuthDep, billing: BillingDep, settings: SettingsDep, body: CheckoutRequest
) -> UrlResponse:
    """JSON alternative to the hosted flow: returns the payment URL for the app to redirect to."""
    destination = app.resolve_return_url(body.success_url)
    success_url, cancel_url = hosted_urls(settings.service_base_url, app.app_id, destination)
    if body.cancel_url:
        cancel_url = app.resolve_return_url(body.cancel_url)
    session = await billing.start_checkout(app, auth, body, success_url, cancel_url)
    return UrlResponse(url=session.url)


@router.post("/billing/portal", response_model=UrlResponse)
async def create_portal_session(
    app: AppDep, auth: AuthDep, billing: BillingDep, body: PortalRequest
) -> UrlResponse:
    """Card updates, cancellations and receipts, handled by the gateway's own portal."""
    url = await billing.portal_url(auth, app.resolve_return_url(body.return_url))
    return UrlResponse(url=url)
