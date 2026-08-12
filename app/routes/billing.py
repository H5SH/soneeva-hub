from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import AppDep, AuthDep, BillingDep, SettingsDep
from app.models.api import CheckoutRequest
from app.utils import get_path, hosted_urls, with_query

router = APIRouter(prefix="/billing/{app_id}", tags=["hosted checkout"])
templates = Jinja2Templates(directory=get_path("app", "templates"))

SEE_OTHER = 303


@router.get("", response_class=HTMLResponse, response_model=None)
async def start(
    request: Request,
    app: AppDep,
    auth: AuthDep,
    billing: BillingDep,
    settings: SettingsDep,
    redirect: str = Query(default="", description="Where to land afterwards"),
    plan: str | None = Query(default=None, description="Skip the picker and go straight to payment")
) -> HTMLResponse | RedirectResponse:
    """Entry point for the hosted flow. The app redirects the merchant here with ?token=."""
    if plan:
        return await checkout(app, auth, billing, settings, plan, redirect)
    record = await billing.get_record(app.id, auth.store_hash)
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
    auth: AuthDep,
    billing: BillingDep,
    settings: SettingsDep,
    plan: str = Query(description="Plan code to buy"),
    redirect: str = Query(default=""),
) -> RedirectResponse:
    """Creates the payment session and hands the merchant to the gateway's hosted page."""
    success_url, cancel_url = hosted_urls(settings.service_base_url, app.id, redirect)
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
    """Public: the gateway sends the merchant here. Syncs state, then bounces back to the app."""
    record = await billing.complete_checkout(app, session_id)
    destination = with_query(
        redirect,
        billing="success" if record else "pending",
        plan=record.plan_code if record else "",
    )
    return RedirectResponse(destination, status_code=SEE_OTHER)


@router.get("/cancel")
async def checkout_cancel(
    app: AppDep, redirect: str = Query(default=None)
) -> RedirectResponse:
    """Public: the merchant backed out of the hosted payment page."""
    return RedirectResponse(
        with_query(redirect, billing="cancelled"), status_code=SEE_OTHER
    )


@router.get("/portal")
async def portal(
    app: AppDep, auth: AuthDep, billing: BillingDep, redirect: str = Query(default=None)
) -> RedirectResponse:
    """Sends the merchant to the gateway's self-service billing page."""
    url = await billing.portal_url(auth, redirect)
    return RedirectResponse(url, status_code=SEE_OTHER)
