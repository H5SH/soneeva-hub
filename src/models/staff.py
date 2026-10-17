"""/v1/payment, /v1/handoff and console billing: bodies and the camelCase views returned."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.models.app import AppConfig, Plan, PlanStatus
from src.models.auth import Application, Tool
from src.models.discount import Coupon, Discount, DiscountKind, Duration
from src.models.mode import Mode
from src.models.payment import Invoice
from src.models.subscription import SubscriptionRecord

APP_ID = r"^[a-z0-9][a-z0-9_-]{1,62}$"


class AppCreate(BaseModel):
    id: str = Field(pattern=APP_ID)
    name: str = Field(default="", max_length=120)
    returnUrl: str = Field(default="", max_length=500)
    allowedReturnHosts: list[str] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=20)


class AppPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    returnUrl: str | None = Field(default=None, max_length=500)
    allowedReturnHosts: list[str] | None = Field(default=None, max_length=50)
    freePlanCode: str | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    webhookSecret: str | None = Field(default=None, max_length=200, description="Write-only; blank keeps it.")
    stagingWebhookSecret: str | None = Field(default=None, max_length=200, description="The same, test mode.")

    def fields(self) -> dict[str, Any]:
        """Stored (snake_case) names; an empty returnUrl or freePlanCode clears it, a blank webhook
        secret leaves it (it is never read back, so a plain save must not wipe it)."""
        names = {"name": "name", "returnUrl": "return_url", "allowedReturnHosts": "allowed_return_hosts",
                 "freePlanCode": "free_plan_code", "tags": "tags"}
        given = self.model_dump(exclude_unset=True)
        secrets = {"stripe_webhook_secret": (given.pop("webhookSecret", None) or "").strip(),
                   "staging_webhook_secret": (given.pop("stagingWebhookSecret", None) or "").strip()}
        return {names[k]: v or None for k, v in given.items() if k in ("returnUrl", "freePlanCode")} | {
            names[k]: v for k, v in given.items()
            if k in ("name", "allowedReturnHosts", "tags") and v is not None
        } | {k: v for k, v in secrets.items() if v}


class PlanPut(BaseModel):
    """Amount is minor units per unit; 0 is the free tier, null keeps the saved one. A different amount,
    unit or interval makes a new Stripe price."""

    name: str = Field(default="", max_length=120)
    description: str | None = Field(default=None, max_length=500)
    amount: int | None = Field(default=None, ge=0)
    currency: str = Field(default="usd", pattern=r"^[a-z]{3}$")
    interval: str = Field(default="month", pattern=r"^(day|week|month|year)$")
    unit: str = "month"
    trialDays: int = Field(default=0, ge=0, le=730)
    features: list[str] = Field(default_factory=list, max_length=50)
    status: PlanStatus = "active"
    contact: bool = False


class PlanCreate(PlanPut):
    """POST: the code is derived from the name ("Pro Plus" -> pro-plus) unless given."""

    code: str = Field(default="", max_length=60, pattern=r"^[a-z0-9-]*$")


class PlanStatusPut(BaseModel):
    status: PlanStatus


class CouponCreate(BaseModel):
    """`value` is a percentage for `percent`, minor units for `amount`. Defaults: 100% off forever."""

    code: str = Field(default="", max_length=40)
    kind: DiscountKind = DiscountKind.PERCENT
    value: float = 100
    currency: str = Field(default="usd", pattern=r"^[a-z]{3}$")
    duration: Duration = Duration.FOREVER
    months: int | None = Field(default=None, ge=1, le=120)
    maxRedemptions: int | None = Field(default=None, ge=1)
    expiresAt: datetime | None = None

    def discount(self) -> Discount:
        """Incoherent terms raise ValueError with a readable sentence, before anything reaches Stripe."""
        return Discount.build(
            kind=self.kind, value=self.value, currency=self.currency, duration=self.duration,
            months=self.months, max_redemptions=self.maxRedemptions, expires_at=self.expiresAt,
        )


class HandoffRequest(BaseModel):
    businessId: str = Field(min_length=1, max_length=64)


class HandoffRedeem(BaseModel):
    code: str = Field(min_length=20, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class CheckoutBody(BaseModel):
    plan: str = Field(min_length=1, max_length=60)
    successUrl: str = Field(max_length=1000)
    cancelUrl: str = Field(max_length=1000)


class PortalBody(BaseModel):
    returnUrl: str = Field(max_length=1000)


def plan_view(plan: Plan) -> dict[str, Any]:
    return {
        "code": plan.code, "name": plan.name, "description": plan.description, "amount": plan.amount,
        "currency": plan.currency, "unit": plan.unit, "interval": plan.interval,
        "trialDays": plan.trial_days, "status": plan.status, "contact": plan.contact,
        "active": plan.active, "productId": plan.product_id, "priceId": plan.price_id,
        "meterId": plan.meter_id, "features": plan.features, "icon": plan.icon,
    }


def app_view(app: AppConfig, application: Application | None, mode: Mode = Mode.LIVE) -> dict[str, Any]:
    """`plans` and `hasWebhookSecret` are `mode`'s; keys are the same in both."""
    keys = application.keys if application else []
    return {
        "id": app.id, "name": app.name, "returnUrl": app.return_url,
        "allowedReturnHosts": app.allowed_return_hosts, "freePlanCode": app.free_plan_code,
        "status": application.status.value if application else "active",
        "scopes": application.scopes if application else [],
        "keys": [{"kid": k.kid, "status": k.status.value, "subject": k.subject} for k in keys],
        "plans": [plan_view(p) for p in app.plans], "tags": app.tags, "mode": mode.value,
        "hasWebhookSecret": bool(application and application.webhook_secret(mode)),
    }


def tool_view(tool: Tool) -> dict[str, Any]:
    return {"id": tool.id, "name": tool.name, "status": tool.status.value, "scopes": tool.scopes,
            "keys": [{"kid": k.kid, "status": k.status.value, "subject": k.subject} for k in tool.keys],
            "subjectKeys": tool.subject_keys}


def coupon_view(coupon: Coupon) -> dict[str, Any]:
    terms = coupon.discount
    return {"code": coupon.code, "appId": coupon.app_id, "maxRedemptions": terms.max_redemptions,
            "timesRedeemed": coupon.times_redeemed, "active": coupon.active, "createdBy": coupon.created_by,
            "couponId": coupon.coupon_id, "promotionCodeId": coupon.promotion_code_id,
            "discount": {"kind": terms.kind.value, "value": terms.value, "currency": terms.currency,
                         "duration": terms.duration.value, "months": terms.months,
                         "expiresAt": terms.expires_at.isoformat() if terms.expires_at else None}}


def subscription_view(record: SubscriptionRecord) -> dict[str, Any]:
    data = record.model_dump(mode="json")
    return {
        "status": data["status"], "active": record.is_active, "planCode": record.plan_code,
        "customerId": record.customer_id, "subscriptionId": record.subscription_id,
        "priceId": record.price_id, "quantity": record.quantity, "amount": record.amount,
        "currency": record.currency, "interval": record.interval,
        "currentPeriodStart": data["current_period_start"], "currentPeriodEnd": data["current_period_end"],
        "trialEnd": data["trial_end"], "cancelAtPeriodEnd": record.cancel_at_period_end,
        "canceledAt": data["canceled_at"], "meterId": record.meter_id,
    }


def invoice_view(invoice: Invoice) -> dict[str, Any]:
    data = invoice.model_dump(mode="json")
    return {
        "id": invoice.id, "number": invoice.number, "status": invoice.status,
        "amountDue": invoice.amount_due, "amountPaid": invoice.amount_paid, "currency": invoice.currency,
        "created": data["created"], "periodStart": data["period_start"], "periodEnd": data["period_end"],
        "hostedInvoiceUrl": invoice.hosted_invoice_url, "invoicePdf": invoice.invoice_pdf,
    }
