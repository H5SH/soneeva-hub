from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import AppDep, BillingDep, HostedDep, SettingsDep
from app.models.api import CheckoutRequest
from app.services.tokens.deps import require_hosted_token
from app.templating import templates
from app.utils import hosted_urls, with_query

router = APIRouter(
    prefix="/billing/{app_id}", tags=["hosted checkout"], dependencies=[Depends(require_hosted_token)]
)

SEE_OTHER = 303


@router.get("", response_class=HTMLResponse, response_model=None)
async def start(
    request: Request,
    app: AppDep,
    auth: HostedDep,
    billing: BillingDep,
    settings: SettingsDep,
    redirect: str = Query(default="", description="Where to land afterwards"),
    plan: str | None = Query(default=None, description="Skip the picker and go straight to payment"),
) -> HTMLResponse | RedirectResponse:
    """Entry point for the hosted flow. The app redirects the customer here with ?token=."""
    if plan:
        return await checkout(app, auth, billing, settings, plan, redirect)
    record = await billing.get_record(app.id, auth.tenant)
    return templates.TemplateResponse(
        request,
        "plans.html",
        {
            "app": app,
            "plans": await billing.list_plans(app, record),
            "record": record,
            "token": request.query_params.get("token"),
            "redirect": redirect,
        },
    )


@router.get("/checkout")
async def checkout(
    app: AppDep,
    auth: HostedDep,
    billing: BillingDep,
    settings: SettingsDep,
    plan: str = Query(description="Plan code to buy"),
    redirect: str = Query(default=""),
) -> RedirectResponse:
    """Creates the payment session and hands the customer to the gateway's hosted page."""
    destination = app.resolve_return_url(redirect)
    success_url, cancel_url = hosted_urls(settings.service_base_url, app.id, destination)
    session = await billing.start_checkout(
        app, auth, CheckoutRequest(plan_code=plan), success_url, cancel_url
    )
    return RedirectResponse(str(session.url), status_code=SEE_OTHER)


@router.get("/return")
async def checkout_return(
    app: AppDep,
    billing: BillingDep,
    session_id: str = Query(description="Gateway checkout session id"),
    redirect: str = Query(default=""),
) -> RedirectResponse:
    """Public: the gateway sends the customer here. Syncs state, then bounces back to the app.

    Unauthenticated by necessity — the browser arrives from Stripe with no token — so the
    redirect target is validated against the app's allow-list rather than trusted.
    """
    record = await billing.complete_checkout(app, session_id)
    destination = with_query(
        app.resolve_return_url(redirect),
        billing="success" if record else "pending",
        plan=record.plan_code if record else "",
    )
    return RedirectResponse(destination, status_code=SEE_OTHER)


@router.get("/cancel")
async def checkout_cancel(app: AppDep, redirect: str = Query(default="")) -> RedirectResponse:
    """Public: the customer backed out of the hosted payment page."""
    return RedirectResponse(
        with_query(app.resolve_return_url(redirect), billing="cancelled"), status_code=SEE_OTHER
    )


@router.get("/portal")
async def portal(
    app: AppDep, auth: HostedDep, billing: BillingDep, redirect: str = Query(default="")
) -> RedirectResponse:
    """Sends the customer to the gateway's self-service billing page."""
    url = await billing.portal_url(auth, app.resolve_return_url(redirect))
    return RedirectResponse(url, status_code=SEE_OTHER)
