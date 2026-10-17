"""Stripe events only drop cached copies and trigger side effects; nothing is stored."""

import logging
from collections.abc import Awaitable, Callable

from src.models.app import AppConfig
from src.models.auth import Application
from src.models.mode import Mode
from src.models.payment import WebhookEvent
from src.models.subscription import GatewaySubscription
from src.payment import PaymentGateway, SignatureError
from src.services.billing import BillingService
from src.services.plans import PlanService
from src.services.tokens.keys import KeyStore
from src.services.ttl import MISSING, Ttl
from src.tunables import tunable

logger = logging.getLogger(__name__)

SUBSCRIPTION_EVENTS = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
    "customer.subscription.trial_will_end",
)
INVOICE_EVENTS = ("invoice.paid", "invoice.payment_failed", "invoice.payment_succeeded")
CHECKOUT_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")
PLAN_EVENTS = ("product.created", "product.updated", "product.deleted",
               "price.created", "price.updated", "price.deleted")
#: Exactly what `handle` acts on, so a provisioned endpoint subscribes to no more than that.
WEBHOOK_EVENTS = SUBSCRIPTION_EVENTS + INVOICE_EVENTS + CHECKOUT_EVENTS + PLAN_EVENTS


class WebhookService:
    def __init__(
        self, payment: PaymentGateway, billing: BillingService, plans: PlanService, keys: KeyStore,
        mode: Mode = Mode.LIVE, on_subscription: Callable[[str, bool], Awaitable[None]] | None = None,
    ) -> None:
        """One per mode. `on_subscription(tenant, active)` runs after each subscription event (checklist)."""
        self._payment = payment
        self._mode = mode
        self._billing = billing
        self._plans = plans
        self._keys = keys
        self._on_subscription = on_subscription
        self._seen = Ttl(tunable("stripe.eventDedupeSeconds"))

    async def parse_for_app(self, app_id: str, payload: bytes, signature: str | None) -> WebhookEvent:
        """Verify with the app's own signing secret for this mode, falling back to the global one. A bad
        signature is a ValueError; any other failure is a bug and surfaces as one."""
        app = await self._keys.get(app_id)
        secret = app.webhook_secret(self._mode) if isinstance(app, Application) else None
        try:
            return self._payment.parse_webhook(payload, signature, secret)
        except SignatureError as error:
            raise ValueError(str(error)) from error

    async def handle(self, app: AppConfig, event: WebhookEvent) -> str:
        """Once per event id (a failed one is retried by Stripe and runs again)."""
        if self._seen.get(event.id) is not MISSING:
            return "duplicate"
        result = await self._apply(app, event)
        self._seen.put(event.id, True)
        return result

    async def _apply(self, app: AppConfig, event: WebhookEvent) -> str:
        """Every endpoint on the account receives every app's events: only this app's apply."""
        if event.type in PLAN_EVENTS:
            if (event.data.get("metadata") or {}).get("app") != app.id:
                return "unmapped"
            self._plans.forget()
            return "processed"
        gateway = await self._subscription(event)
        if not gateway:
            return "ignored"
        tenant = gateway.metadata.get("tenant")
        if not tenant or gateway.metadata.get("app") != app.id:
            logger.warning(f"Event {event.id} carries metadata {gateway.metadata}, expected app {app.id}")
            return "unmapped"
        record = await self._billing.sync(app.id, tenant)
        if self._on_subscription:
            await self._on_subscription(tenant, record.is_active)
        return "processed"

    async def _subscription(self, event: WebhookEvent) -> GatewaySubscription | None:
        if event.type in SUBSCRIPTION_EVENTS:
            return self._payment.parse_subscription(event.data)
        if event.type in CHECKOUT_EVENTS or event.type in INVOICE_EVENTS:
            subscription_id = self._subscription_id(event)
            return await self._payment.get_subscription(subscription_id) if subscription_id else None
        return None

    @staticmethod
    def _subscription_id(event: WebhookEvent) -> str | None:
        """Checkout sessions carry it directly; invoices moved it under `parent` in newer APIs."""
        subscription = event.data.get("subscription")
        if isinstance(subscription, dict):
            return subscription.get("id")
        if subscription:
            return subscription
        parent = event.data.get("parent") or {}
        return (parent.get("subscription_details") or {}).get("subscription")
