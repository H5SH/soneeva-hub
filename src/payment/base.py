from abc import ABC, abstractmethod
from typing import Any

from src.models.app import Plan
from src.models.discount import Coupon, Discount
from src.models.payment import CheckoutSession, Invoice, PaymentMethod, WebhookEvent
from src.models.subscription import GatewaySubscription


class SignatureError(Exception):
    """The provider rejected the webhook signature. Distinct from a bug in our own handling."""


class PaymentGateway(ABC):
    """Provider contract and the only holder of payment data. Everything returned is neutral."""

    # ── plans: one product per plan, its default price ──────────────────────
    @abstractmethod
    async def list_plans(self) -> list[Plan]:
        """Every live plan of every app, read consistently (no search index)."""

    @abstractmethod
    async def create_plan(self, plan: Plan) -> Plan: ...

    @abstractmethod
    async def update_plan(self, current: Plan, wanted: Plan) -> Plan:
        """Prices are immutable: a different amount, unit or interval makes a new default price and
        archives the old one; subscribers keep billing on it."""

    @abstractmethod
    async def archive_plan(self, plan: Plan) -> None: ...

    @abstractmethod
    async def set_plan_icon(self, plan: Plan, data: bytes | None, content_type: str = "") -> str | None:
        """Upload the image as the product's only image and return its public URL; None removes it."""

    # ── customers and subscriptions: metadata {app, tenant} ─────────────────
    @abstractmethod
    async def find_customer(self, app_id: str, tenant: str) -> str | None:
        """Consistent: covers customers made too recently for the search index."""

    @abstractmethod
    async def create_customer(self, app_id: str, tenant: str, name: str) -> str: ...

    @abstractmethod
    async def customer_subscription(self, customer_id: str) -> GatewaySubscription | None:
        """The newest live subscription, else the newest one."""

    @abstractmethod
    async def app_subscriptions(self, app_id: str) -> list[GatewaySubscription]:
        """Every subscription of the app, for listings (may lag the newest by the search index)."""

    @abstractmethod
    async def get_subscription(self, subscription_id: str) -> GatewaySubscription | None: ...

    @abstractmethod
    async def change_subscription_plan(
        self, subscription_id: str, price_id: str, proration_behavior: str
    ) -> GatewaySubscription: ...

    @abstractmethod
    async def cancel_subscription(self, subscription_id: str, at_period_end: bool) -> GatewaySubscription: ...

    @abstractmethod
    async def resume_subscription(self, subscription_id: str) -> GatewaySubscription: ...

    @abstractmethod
    async def create_checkout_session(
        self, customer_id: str, price_id: str, success_url: str, cancel_url: str,
        metadata: dict[str, str], quantity: int = 1, trial_days: int | None = None,
        promo_code: str | None = None, metered: bool = False,
    ) -> CheckoutSession: ...

    @abstractmethod
    async def create_portal_session(self, customer_id: str, return_url: str) -> str: ...

    @abstractmethod
    async def list_invoices(
        self, customer_id: str, limit: int = 20, starting_after: str | None = None
    ) -> tuple[list[Invoice], bool]: ...

    @abstractmethod
    async def get_default_payment_method(self, customer_id: str) -> PaymentMethod | None: ...

    # ── usage: billing meters ───────────────────────────────────────────────
    @abstractmethod
    async def record_usage(self, meter_id: str, customer_id: str, value: int, identifier: str) -> bool:
        """One meter event; False when `identifier` was already recorded."""

    @abstractmethod
    async def usage_days(self, meter_id: str, customer_id: str, days: int) -> list[dict[str, Any]]:
        """`[{day, seconds}]`, newest first, for the last `days` UTC days."""

    # ── coupons: a coupon and its promotion code, metadata {app} ────────────
    @abstractmethod
    async def list_coupons(self, app_id: str) -> list[Coupon]: ...

    @abstractmethod
    async def find_coupon(self, code: str) -> Coupon | None:
        """The active promotion code with this code, whichever app owns it."""

    @abstractmethod
    async def create_coupon(
        self, app_id: str, code: str, product_ids: list[str], discount: Discount, created_by: str
    ) -> Coupon: ...

    @abstractmethod
    async def delete_coupon(self, coupon: Coupon) -> None: ...

    # ── webhooks ────────────────────────────────────────────────────────────
    @abstractmethod
    async def create_webhook_endpoint(self, url: str, events: list[str]) -> tuple[str, str]:
        """Returns (endpoint id, signing secret), the secret only now."""

    @abstractmethod
    def parse_webhook(self, payload: bytes, signature: str | None, secret: str | None = None) -> WebhookEvent:
        """Verify the signature (the app's own `secret`, else the default) and normalise.
        Raises SignatureError on a bad signature."""

    @abstractmethod
    def parse_subscription(self, payload: Any) -> GatewaySubscription: ...
