"""In-memory Stripe for tests and offline runs: the same contract, recording what it was asked."""

import json
from typing import Any

from src.models.app import Plan
from src.models.discount import Coupon, Discount
from src.models.payment import CheckoutSession, Invoice, PaymentMethod, WebhookEvent
from src.models.subscription import GatewaySubscription, SubscriptionStatus
from src.payment.base import PaymentGateway
from src.payment.stripe.catalog import same_price
from src.payment.stripe.gateway import newest


class FakePayment(PaymentGateway):
    def __init__(self) -> None:
        self.products: dict[str, Plan] = {}
        self.prices: list[str] = []
        self.archived: list[str] = []
        self.customers: dict[str, dict[str, str]] = {}
        self.subscriptions: dict[str, GatewaySubscription] = {}
        self.coupons: dict[str, Coupon] = {}
        self.coupon_products: dict[str, list[str]] = {}
        self.usage: dict[str, tuple[str, str, int]] = {}
        self.endpoints: list[tuple[str, list[str]]] = []
        self.checkouts: list[dict[str, Any]] = []
        self.webhook_secrets: list[str | None] = []
        self.calls = 0

    # ── plans ───────────────────────────────────────────────────────────────
    def _priced(self, plan: Plan) -> Plan:
        if not plan.amount:
            return plan.model_copy(update={"price_id": None, "meter_id": None})
        self.prices.append(f"price_{plan.code}_{len(self.prices) + 1}")
        meter = f"mtr_{plan.app}_{plan.code}" if plan.metered else None
        return plan.model_copy(update={"price_id": self.prices[-1], "meter_id": meter})

    async def list_plans(self) -> list[Plan]:
        self.calls += 1
        return [p for p in self.products.values() if p.app]

    async def create_plan(self, plan: Plan) -> Plan:
        plan = self._priced(plan.model_copy(update={"product_id": f"prod_{plan.app}_{plan.code}"}))
        self.products[str(plan.product_id)] = plan
        return plan

    async def update_plan(self, current: Plan, wanted: Plan) -> Plan:
        wanted = wanted.model_copy(update={"product_id": current.product_id, "price_id": current.price_id,
                                           "meter_id": current.meter_id, "icon": current.icon})
        if not same_price(current, wanted):
            if current.price_id:
                self.archived.append(current.price_id)
            wanted = self._priced(wanted)
        self.products[str(current.product_id)] = wanted
        return wanted

    async def archive_plan(self, plan: Plan) -> None:
        if plan.price_id:
            self.archived.append(plan.price_id)
        self.products.pop(str(plan.product_id), None)

    async def set_plan_icon(self, plan: Plan, data: bytes | None, content_type: str = "") -> str | None:
        url = f"https://files.stripe.test/{plan.code}" if data else None
        self.products[str(plan.product_id)] = plan.model_copy(update={"icon": url})
        return url

    # ── customers and subscriptions ─────────────────────────────────────────
    async def find_customer(self, app_id: str, tenant: str) -> str | None:
        self.calls += 1
        wanted = {"app": app_id, "tenant": tenant}
        return next((c for c, meta in self.customers.items() if meta == wanted), None)

    async def create_customer(self, app_id: str, tenant: str, name: str) -> str:
        self.customers[f"cus_{tenant}"] = {"app": app_id, "tenant": tenant}
        return f"cus_{tenant}"

    async def customer_subscription(self, customer_id: str) -> GatewaySubscription | None:
        self.calls += 1
        return newest([s for s in self.subscriptions.values() if s.customer_id == customer_id])

    async def app_subscriptions(self, app_id: str) -> list[GatewaySubscription]:
        return [s for s in self.subscriptions.values() if s.metadata.get("app") == app_id]

    async def get_subscription(self, subscription_id: str) -> GatewaySubscription | None:
        return self.subscriptions.get(subscription_id)

    def subscribe(self, app_id: str, tenant: str, plan: Plan, sub_id: str = "sub_1",
                  status: SubscriptionStatus = SubscriptionStatus.ACTIVE) -> GatewaySubscription:
        """What a completed checkout leaves in Stripe."""
        customer = f"cus_{tenant}"
        self.customers[customer] = {"app": app_id, "tenant": tenant}
        self.subscriptions[sub_id] = GatewaySubscription(
            subscription_id=sub_id, customer_id=customer, status=status, price_id=plan.price_id,
            meter_id=plan.meter_id, plan_code=plan.code, amount=plan.amount, currency=plan.currency,
            created=len(self.subscriptions), metadata={"app": app_id, "tenant": tenant})
        return self.subscriptions[sub_id]

    def _update(self, subscription_id: str, **fields: Any) -> GatewaySubscription:
        updated = self.subscriptions[subscription_id].model_copy(update=fields)
        self.subscriptions[subscription_id] = updated
        return updated

    async def change_subscription_plan(self, subscription_id, price_id, proration_behavior):
        plan = next(p for p in self.products.values() if p.price_id == price_id)
        return self._update(subscription_id, price_id=price_id, plan_code=plan.code, meter_id=plan.meter_id)

    async def cancel_subscription(self, subscription_id, at_period_end):
        if at_period_end:
            return self._update(subscription_id, cancel_at_period_end=True)
        return self._update(subscription_id, status=SubscriptionStatus.CANCELED)

    async def resume_subscription(self, subscription_id):
        return self._update(subscription_id, cancel_at_period_end=False)

    async def create_checkout_session(self, customer_id, price_id, success_url, cancel_url, metadata,
                                      quantity=1, trial_days=None, promo_code=None, metered=False):
        self.checkouts.append({"customer_id": customer_id, "price_id": price_id, "metadata": metadata,
                               "trial_days": trial_days, "metered": metered})
        return CheckoutSession(id="cs_1", url="https://checkout.stripe.test/cs_1")

    async def create_portal_session(self, customer_id: str, return_url: str) -> str:
        return f"https://billing.stripe.test/{customer_id}"

    async def list_invoices(self, customer_id, limit=20, starting_after=None):
        return [Invoice(id="in_1", number="INV-1", status="paid", amount_due=120, amount_paid=120)], False

    async def get_default_payment_method(self, customer_id: str) -> PaymentMethod | None:
        return PaymentMethod(id="pm_1", brand="visa", last4="4242")

    # ── usage ───────────────────────────────────────────────────────────────
    async def record_usage(self, meter_id: str, customer_id: str, value: int, identifier: str) -> bool:
        if identifier in self.usage:
            return False
        self.usage[identifier] = (meter_id, customer_id, value)
        return True

    async def usage_days(self, meter_id: str, customer_id: str, days: int) -> list[dict[str, Any]]:
        seconds = sum(v for m, c, v in self.usage.values() if (m, c) == (meter_id, customer_id))
        return [{"day": "2026-10-15", "seconds": seconds}] if seconds else []

    # ── coupons ─────────────────────────────────────────────────────────────
    async def list_coupons(self, app_id: str) -> list[Coupon]:
        return [c for c in self.coupons.values() if c.app_id == app_id]

    async def find_coupon(self, code: str) -> Coupon | None:
        return self.coupons.get(code)

    async def create_coupon(self, app_id, code, product_ids, discount: Discount, created_by) -> Coupon:
        self.coupons[code] = Coupon(code=code, app_id=app_id, coupon_id=f"coup_{code}",
                                    promotion_code_id=f"promo_{code}", created_by=created_by,
                                    discount=discount)
        self.coupon_products[code] = product_ids
        return self.coupons[code]

    async def delete_coupon(self, coupon: Coupon) -> None:
        self.coupons.pop(coupon.code, None)

    # ── webhooks ────────────────────────────────────────────────────────────
    async def create_webhook_endpoint(self, url: str, events: list[str]) -> tuple[str, str]:
        self.endpoints.append((url, list(events)))
        return f"we_{len(self.endpoints)}", f"whsec_{len(self.endpoints)}"

    def parse_webhook(self, payload: bytes, signature: str | None, secret: str | None = None) -> WebhookEvent:
        self.webhook_secrets.append(secret)
        event = json.loads(payload)
        return WebhookEvent(id=event["id"], type=event["type"], data=event["data"]["object"])

    def parse_subscription(self, payload: Any) -> GatewaySubscription:
        return self.subscriptions[payload["id"]]
