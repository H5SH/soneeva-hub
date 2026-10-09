from abc import ABC, abstractmethod
from typing import Any

from app.models.payment import CheckoutSession, Invoice, PaymentMethod, Price, WebhookEvent
from app.models.subscription import GatewaySubscription


class SignatureError(Exception):
    """The provider rejected the webhook signature. Distinct from a bug in our own handling."""


class PaymentGateway(ABC):
    """Provider contract. Everything returned is provider-neutral, so the gateway is swappable."""

    @abstractmethod
    async def create_customer(
        self, metadata: dict[str, str], name: str | None = None
    ) -> str:
        """Returns the provider customer id."""

    @abstractmethod
    async def find_customer(self, app_id: str, store_hash: str) -> str | None:
        """Recover a customer id from provider-side metadata when the local row is gone."""

    @abstractmethod
    async def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
        quantity: int = 1,
        trial_days: int | None = None,
        promo_code: str | None = None,
    ) -> CheckoutSession:
        """Hosted payment page the merchant is redirected to."""

    @abstractmethod
    async def get_checkout_session(self, session_id: str) -> CheckoutSession | None: ...

    @abstractmethod
    async def create_portal_session(self, customer_id: str, return_url: str) -> str:
        """Hosted self-service billing page. Returns its URL."""

    @abstractmethod
    async def get_subscription(self, subscription_id: str) -> GatewaySubscription | None: ...

    @abstractmethod
    async def find_subscription(self, customer_id: str) -> GatewaySubscription | None:
        """Newest live subscription for the customer, used to recover from lost local state."""

    @abstractmethod
    async def change_subscription_plan(
        self, subscription_id: str, price_id: str, proration_behavior: str
    ) -> GatewaySubscription: ...

    @abstractmethod
    async def cancel_subscription(
        self, subscription_id: str, at_period_end: bool
    ) -> GatewaySubscription: ...

    @abstractmethod
    async def resume_subscription(self, subscription_id: str) -> GatewaySubscription:
        """Undo a pending end-of-period cancellation."""

    @abstractmethod
    async def list_invoices(
        self, customer_id: str, limit: int = 20, starting_after: str | None = None
    ) -> tuple[list[Invoice], bool]:
        """Returns (invoices, has_more)."""

    @abstractmethod
    async def get_default_payment_method(self, customer_id: str) -> PaymentMethod | None: ...

    @abstractmethod
    async def get_price(self, price_id: str) -> Price | None: ...

    @abstractmethod
    async def create_price(
        self,
        app_id: str,
        plan_code: str,
        name: str,
        amount: int,
        currency: str,
        interval: str,
    ) -> tuple[str, str]:
        """Create a product and a recurring price. Returns (product_id, price_id)."""

    @abstractmethod
    async def revenue_by_day(self, days: int) -> list[tuple[str, int]]:
        """Succeeded charges per day, oldest first, as (ISO date, minor units)."""

    @abstractmethod
    async def can_write_products(self) -> tuple[bool, str]:
        """Whether this key may create products and prices, and why not when it may not."""

    @abstractmethod
    async def create_full_discount(
        self, code: str, app_id: str, product_ids: list[str], max_redemptions: int | None
    ) -> tuple[str, str]:
        """A 100%-off coupon exposed as a promotion code. Returns (coupon_id, promo_id)."""

    @abstractmethod
    async def delete_promotion_code(self, promotion_code_id: str) -> None:
        """Deactivate a promotion code so it stops being redeemable."""

    @abstractmethod
    def parse_webhook(
        self, payload: bytes, signature: str | None, secret: str | None = None
    ) -> WebhookEvent:
        """Verify the provider signature and normalise the event.

        Raises SignatureError on a bad signature. `secret` overrides the gateway default so each
        app can carry its own signing secret.
        """

    @abstractmethod
    def parse_subscription(self, payload: dict[str, Any]) -> GatewaySubscription:
        """Normalise a raw subscription object delivered inside a webhook."""
