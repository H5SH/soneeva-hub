"""Stripe objects to provider-neutral models. Every SDK value crosses through `plain` first."""

from datetime import UTC, datetime
from typing import Any

from src.models.app import Plan
from src.models.discount import Coupon, Discount, DiscountKind
from src.models.payment import CheckoutSession, Invoice, PaymentMethod
from src.models.subscription import GatewaySubscription, SubscriptionStatus


def plain(obj: Any) -> dict[str, Any]:
    """A Stripe object as an ordinary dict, nested objects and lists included.

    `StripeObject` is not a dict in current SDK versions: `.get()` raises AttributeError and
    `dict(obj)` raises KeyError, both only against the live API. One place to be wrong.
    """
    if obj is None:
        return {}
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def at(timestamp: int | None) -> datetime | None:
    return datetime.fromtimestamp(timestamp, UTC) if timestamp else None


def ref(value: Any) -> str | None:
    """An expandable field: either the id itself or the object carrying it."""
    return value if isinstance(value, str) else (value or {}).get("id")


def to_invoice(row: Any) -> Invoice:
    row = plain(row)
    return Invoice(
        id=row["id"],
        number=row.get("number"),
        status=row.get("status"),
        amount_due=row.get("amount_due") or 0,
        amount_paid=row.get("amount_paid") or 0,
        currency=row.get("currency"),
        created=at(row.get("created")),
        period_start=at(row.get("period_start")),
        period_end=at(row.get("period_end")),
        hosted_invoice_url=row.get("hosted_invoice_url"),
        invoice_pdf=row.get("invoice_pdf"),
    )


def to_subscription(payload: Any) -> GatewaySubscription:
    payload = plain(payload)
    item = ((payload.get("items") or {}).get("data") or [{}])[0]
    price = item.get("price") or {}
    recurring = price.get("recurring") or {}
    return GatewaySubscription(
        subscription_id=payload["id"],
        customer_id=ref(payload.get("customer")),
        status=SubscriptionStatus(payload["status"]),
        price_id=price.get("id"),
        meter_id=recurring.get("meter"),
        plan_code=(price.get("metadata") or {}).get("code"),
        quantity=item.get("quantity") or 1,
        amount=price.get("unit_amount"),
        currency=price.get("currency") or payload.get("currency"),
        interval=recurring.get("interval"),
        current_period_start=at(item.get("current_period_start") or payload.get("current_period_start")),
        current_period_end=at(item.get("current_period_end") or payload.get("current_period_end")),
        trial_end=at(payload.get("trial_end")),
        cancel_at_period_end=bool(payload.get("cancel_at_period_end")),
        canceled_at=at(payload.get("canceled_at")),
        latest_invoice_id=ref(payload.get("latest_invoice")),
        created=payload.get("created") or 0,
        metadata=dict(payload.get("metadata") or {}),
    )


def to_checkout(payload: Any) -> CheckoutSession:
    payload = plain(payload)
    return CheckoutSession(
        id=payload["id"],
        url=payload.get("url"),
        customer_id=ref(payload.get("customer")),
        subscription_id=ref(payload.get("subscription")),
        status=payload.get("status"),
        payment_status=payload.get("payment_status"),
        metadata=dict(payload.get("metadata") or {}),
    )


def to_payment_method(method: Any) -> PaymentMethod:
    method = plain(method)
    card = method.get("card") or {}
    return PaymentMethod(
        id=method.get("id"),
        brand=card.get("brand"),
        last4=card.get("last4"),
        exp_month=card.get("exp_month"),
        exp_year=card.get("exp_year"),
        wallet=(card.get("wallet") or {}).get("type"),
    )


def to_plan(product: Any) -> Plan:
    """A product with its expanded `default_price`."""
    product = plain(product)
    meta = product.get("metadata") or {}
    price = product.get("default_price")
    price = price if isinstance(price, dict) else {}
    recurring = price.get("recurring") or {}
    return Plan(
        app=meta.get("app", ""), code=meta.get("code", ""), name=product.get("name", ""),
        description=product.get("description"), product_id=product["id"], price_id=price.get("id"),
        meter_id=recurring.get("meter"), amount=price.get("unit_amount"),
        currency=price.get("currency") or "usd", interval=recurring.get("interval") or "month",
        unit=meta.get("unit") or "month", trial_days=int(meta.get("trialDays") or 0) or None,
        status=meta.get("status") or "active", contact=meta.get("contact") == "true",
        features=[f["name"] for f in product.get("marketing_features") or [] if f.get("name")],
        icon=(product.get("images") or [None])[0],
    )


def to_coupon(promotion: Any) -> Coupon:
    """A promotion code with its coupon expanded (`promotion.coupon`, or `coupon` on older APIs)."""
    promotion = plain(promotion)
    coupon = (promotion.get("promotion") or {}).get("coupon") or promotion.get("coupon") or {}
    percent = coupon.get("percent_off")
    discount = Discount(
        kind=DiscountKind.PERCENT if percent is not None else DiscountKind.AMOUNT,
        value=percent if percent is not None else coupon.get("amount_off") or 0,
        currency=coupon.get("currency") or "usd", duration=coupon.get("duration") or "forever",
        months=coupon.get("duration_in_months"), max_redemptions=promotion.get("max_redemptions"),
        expires_at=at(promotion.get("expires_at")),
    )
    meta = promotion.get("metadata") or {}
    return Coupon(
        code=promotion["code"], app_id=meta.get("app", ""), coupon_id=coupon.get("id", ""),
        promotion_code_id=promotion["id"], created_by=meta.get("createdBy", ""), discount=discount,
        times_redeemed=promotion.get("times_redeemed") or 0, active=bool(promotion.get("active")),
    )
