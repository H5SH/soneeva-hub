import logging

from app.auth.keys import KeyStore
from app.models.app import AppConfig
from app.models.db import Collection
from app.models.payment import WebhookEvent
from app.models.subscription import GatewaySubscription
from app.services.billing import BillingService
from app.services.db import Database
from app.services.payment import PaymentGateway, SignatureError

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


class WebhookService:
    """Keeps stored subscriptions in step with the gateway. Every event is handled once."""

    def __init__(
        self,
        db: Database,
        payment: PaymentGateway,
        billing: BillingService,
        keys: KeyStore,
    ) -> None:
        self._db = db
        self._payment = payment
        self._billing = billing
        self._keys = keys

    async def parse_for_app(
        self, app_id: str, payload: bytes, signature: str | None
    ) -> WebhookEvent:
        """Verify using the app's own signing secret, falling back to the global one."""
        application = await self._keys.get(app_id)
        return self.parse(payload, signature, application.stripe_webhook_secret if application else None)

    def parse(self, payload: bytes, signature: str | None, secret: str | None = None) -> WebhookEvent:
        """Raises ValueError on a bad signature, so routes stay free of provider exceptions.

        Narrow on purpose: only signature failures become ValueError. A bare `except Exception`
        here reported genuine bugs to the caller as a 400, hiding them behind "invalid signature".
        """
        try:
            return self._payment.parse_webhook(payload, signature, secret)
        except SignatureError as error:
            raise ValueError(str(error)) from error

    async def handle(self, app: AppConfig, event: WebhookEvent) -> str:
        if not await self._db.create_row(
            Collection.EVENTS,
            event.id,
            {"app_id": app.id, "type": event.type, "created": event.created},
        ):
            return "duplicate"

        gateway = await self._resolve_subscription(event)
        if not gateway:
            return "ignored"

        store_hash = gateway.metadata.get("store_hash")
        event_app_id = gateway.metadata.get("app_id")
        if not store_hash or event_app_id != app.id:
            logger.warning(
                f"Event {event.id} carries metadata {gateway.metadata}, expected app {app.id}"
            )
            return "unmapped"

        await self._billing.save_subscription(app, store_hash, gateway)
        return "processed"

    async def _resolve_subscription(self, event: WebhookEvent) -> GatewaySubscription | None:
        if event.type in SUBSCRIPTION_EVENTS:
            parsed = self._payment.parse_subscription(event.data)
            if event.type == "customer.subscription.deleted":
                return parsed
            return await self._payment.get_subscription(parsed.subscription_id) or parsed

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
