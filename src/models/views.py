"""How plans, apps, coupons and subscriptions look in the staff API."""

from typing import Any

from src.models.app import AppConfig, Plan
from src.models.auth import Application, Tool
from src.models.discount import Coupon
from src.models.mode import Mode
from src.models.payment import Invoice
from src.models.subscription import SubscriptionRecord


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
