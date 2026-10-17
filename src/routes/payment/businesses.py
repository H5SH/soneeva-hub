"""Businesses as billing customers, from Stripe: subscription, invoices, daily usage; no totals."""

from fastapi import APIRouter

from src.dependencies import AdminDep, CustomerDep
from src.models.admin import Principal, Scope
from src.models.staff import invoice_view, plan_view, subscription_view
from src.services.tokens.staff import requires_staff

router = APIRouter(prefix="/v1/payment/businesses", tags=["payment"])
VIEW = Scope.HUB_PAYMENT_VIEW


@router.get("")
async def list_businesses(
    customers: CustomerDep, person: Principal = requires_staff(VIEW, Scope.BUSINESS_VIEW)
) -> dict:
    rows = await customers.every(person)
    return {"businesses": [{"business": b.model_dump(mode="json"), "subscription": subscription_view(r)}
                           for b, r in rows]}


@router.get("/{business_id}")
async def get_business(
    business_id: str, customers: CustomerDep, admin: AdminDep,
    person: Principal = requires_staff(VIEW, Scope.BUSINESS_VIEW),
) -> dict:
    business, record = await customers.one(person, business_id)
    app = await admin.get_app(business.appId, business.mode)
    plan = app.plan(record.plan_code) if app else None
    return {"business": business.model_dump(mode="json"), "subscription": subscription_view(record),
            "plan": plan_view(plan) if plan else None}


@router.get("/{business_id}/invoices")
async def list_invoices(
    business_id: str, customers: CustomerDep, person: Principal = requires_staff(VIEW, Scope.INVOICE_VIEW)
) -> dict:
    """Straight from Stripe, newest first (`customers.invoicesShown`)."""
    business, record = await customers.one(person, business_id)
    return {"invoices": [invoice_view(i) for i in await customers.invoices(business, record)]}


@router.get("/{business_id}/usage")
async def usage(
    business_id: str, customers: CustomerDep, person: Principal = requires_staff(VIEW, Scope.USAGE_VIEW)
) -> dict:
    """Seconds per day from the Stripe meter (`customers.usageDaysShown`)."""
    return await customers.usage(*await customers.one(person, business_id))
