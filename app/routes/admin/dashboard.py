"""The landing page, the revenue panel it fetches, and the integration guide."""

import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.dependencies import AdminDep, PaymentDep, PrincipalDep, SettingsDep
from app.models.admin import Scope
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


def _money(minor: int) -> str:
    return f"${minor / 100:,.2f}"


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, user: PrincipalDep, admin: AdminDep):
    apps, keys = await admin.list_apps_with_keys(user) if user.may(Scope.APP_VIEW) else ([], {})
    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {
            "user": user,
            "nav": "dashboard",
            "apps": apps,
            "key_counts": keys,
            "counts": await admin.counts(user, apps),
        },
    )


@router.get("/revenue", response_class=HTMLResponse)
async def revenue(request: Request, user: PrincipalDep, payment: PaymentDep):
    """The revenue panel on its own, fetched after the dashboard has painted.

    Stripe is the slowest call in the service and the dashboard does not depend on it, so
    blocking the first byte on it made every other number late. Served as HTML rather than
    JSON to keep the chart in one place — the Jinja macro.
    """
    if not user.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superadmin only")
    try:
        series = await payment.revenue_by_day(30)
    except Exception:
        logging.exception("revenue lookup failed")
        return templates.TemplateResponse(request, "admin/_revenue.html", {"error": True})

    best = max(series, key=lambda row: row[1], default=("", 0))
    return templates.TemplateResponse(
        request,
        "admin/_revenue.html",
        {
            "revenue": series if any(amount for _, amount in series) else None,
            "total": _money(sum(amount for _, amount in series)),
            "best_day": best[0],
            "best_amount": _money(best[1]),
        },
    )


@router.get("/guide", response_class=HTMLResponse)
async def guide(request: Request, settings: SettingsDep, user: PrincipalDep):
    return templates.TemplateResponse(
        request,
        "admin/guide.html",
        {"user": user, "nav": "guide", "base_url": settings.service_base_url},
    )
