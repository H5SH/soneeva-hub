"""The console's billing page: choosable plans, Stripe Checkout and the customer portal.

Business-subject tokens only; the person needs `billing.view` in that business. A test business
bills in Stripe test mode.
"""

from urllib.parse import urlparse

from fastapi import APIRouter

from src.dependencies import BillingsDep, BusinessDep, RegistryDep
from src.models.admin import Scope
from src.models.api import CheckoutRequest
from src.models.app import AppConfig
from src.models.auth import AuthContext
from src.models.people import Actor
from src.models.staff import CheckoutBody, PortalBody, plan_view, subscription_view
from src.services.billing import BillingService
from src.services.errors import PlatformError
from src.services.people.access import requires_actor

router = APIRouter(prefix="/v1/businesses/{business_id}", tags=["billing"])
BILLING_SCOPE = "billing.view"


async def _context(
    actor: Actor, business_id: str, businesses, registry, billings
) -> tuple[AppConfig, AuthContext, BillingService]:
    if actor.business_id != business_id:
        raise PlatformError("Call with sub = businessId", 403)
    if BILLING_SCOPE not in actor.scopes:
        raise PlatformError(f"Requires {BILLING_SCOPE}", 403)
    business = await businesses.require(None, business_id)
    app = await registry.get(business.appId, business.mode)
    if not app:
        raise PlatformError("The business's app is not registered", 404)
    return app, AuthContext(app_id=app.id, tenant=business.id), billings[business.mode]


def _allowed(app: AppConfig, url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in app.allowed_return_hosts:
        raise PlatformError(f"{url} is not on the app's allowed hosts", 422)
    return url


@router.get("/plans")
async def plans(
    business_id: str, businesses: BusinessDep, registry: RegistryDep, billings: BillingsDep,
    actor: Actor = requires_actor(Scope.HUB_BILLING_VIEW),
) -> dict:
    """Plans on sale, the current one marked, and the subscription; all from Stripe."""
    app, auth, billing = await _context(actor, business_id, businesses, registry, billings)
    record = await billing.get_record(app.id, auth.tenant)
    return {"plans": [plan_view(p) | {"current": p.is_current} for p in billing.list_plans(app, record)],
            "subscription": subscription_view(record)}


@router.post("/checkout")
async def checkout(
    business_id: str, body: CheckoutBody, businesses: BusinessDep, registry: RegistryDep,
    billings: BillingsDep, actor: Actor = requires_actor(Scope.HUB_BILLING_UPDATE),
) -> dict:
    """A Stripe Checkout URL. 409 while a subscription is active (use the portal)."""
    app, auth, billing = await _context(actor, business_id, businesses, registry, billings)
    urls = _allowed(app, body.successUrl), _allowed(app, body.cancelUrl)
    session = await billing.start_checkout(app, auth, CheckoutRequest(plan_code=body.plan), *urls)
    return {"url": session.url}


@router.post("/portal")
async def portal(
    business_id: str, body: PortalBody, businesses: BusinessDep, registry: RegistryDep,
    billings: BillingsDep, actor: Actor = requires_actor(Scope.HUB_BILLING_UPDATE),
) -> dict:
    """A Stripe customer portal URL (card, invoices, cancel). 404 before the first checkout."""
    app, auth, billing = await _context(actor, business_id, businesses, registry, billings)
    return {"url": await billing.portal_url(auth, _allowed(app, body.returnUrl))}
