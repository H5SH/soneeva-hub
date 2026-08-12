from abc import ABC, abstractmethod
from typing import Any

from app.models.payment import CheckoutSession, Invoice, PaymentMethod, Price, WebhookEvent
from app.models.subscription import GatewaySubscription


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
    def parse_webhook(self, payload: bytes, signature: str | None) -> WebhookEvent:
        """Verify the provider signature and normalise the event. Raises on a bad signature."""

    @abstractmethod
    def parse_subscription(self, payload: dict[str, Any]) -> GatewaySubscription:
        """Normalise a raw subscription object delivered inside a webhook."""
