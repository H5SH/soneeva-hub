import time
from typing import Any

import stripe

from src.models.payment import CheckoutSession, Invoice, PaymentMethod, WebhookEvent
from src.models.subscription import GatewaySubscription
from src.payment.base import PaymentGateway, SignatureError
from src.payment.stripe.catalog import StripeCatalog
from src.payment.stripe.parse import (
    at,
    plain,
    to_checkout,
    to_invoice,
    to_payment_method,
    to_subscription,
)
from src.tunables import tunable

LIVE_STATUSES = ("trialing", "active", "past_due", "unpaid", "paused", "incomplete")


def newest(subscriptions: list[GatewaySubscription]) -> GatewaySubscription | None:
    """The newest live subscription, else the newest of any status."""
    live = [s for s in subscriptions if s.status in LIVE_STATUSES] or subscriptions
    return max(live, key=lambda s: s.created, default=None)


def _quote(value: str) -> str:
    """Escape a value for Stripe's search query language."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class StripeGateway(StripeCatalog, PaymentGateway):
    """Stripe implementation of the payment contract."""

    def __init__(self, api_key: str, webhook_secret: str) -> None:
        self._api = stripe.StripeClient(api_key).v1
        self._webhook_secret = webhook_secret
        self._meter_events = {}

    async def create_customer(self, app_id: str, tenant: str, name: str) -> str:
        customer = await self._api.customers.create_async(
            {"name": name, "metadata": {"app": app_id, "tenant": tenant}})
        return plain(customer)["id"]

    async def find_customer(self, app_id: str, tenant: str) -> str | None:
        """Search for indexed customers; the search index lags creation by up to a minute, so the
        last `stripe.searchLagSeconds` of customers are listed and matched as well."""
        wanted = {"app": app_id, "tenant": tenant}
        query = f"metadata['app']:'{_quote(app_id)}' AND metadata['tenant']:'{_quote(tenant)}'"
        found = await self._api.customers.search_async({"query": query, "limit": 1})
        if found.data:
            return plain(found.data[0])["id"]
        since = int(time.time()) - tunable("stripe.searchLagSeconds")
        page = await self._api.customers.list_async({"created": {"gte": since}, "limit": 100})
        async for customer in page.auto_paging_iter():
            row = plain(customer)
            if wanted.items() <= (row.get("metadata") or {}).items():
                return row["id"]
        return None

    async def customer_subscription(self, customer_id: str) -> GatewaySubscription | None:
        result = await self._api.subscriptions.list_async(
            {"customer": customer_id, "status": "all", "limit": 10})
        return newest([to_subscription(s) for s in result.data])

    async def app_subscriptions(self, app_id: str) -> list[GatewaySubscription]:
        page = await self._api.subscriptions.search_async(
            {"query": f"metadata['app']:'{_quote(app_id)}'", "limit": 100})
        return [to_subscription(s) async for s in page.auto_paging_iter()]

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
        metered: bool = False,
    ) -> CheckoutSession:
        item: dict[str, Any] = {"price": price_id} if metered else {"price": price_id, "quantity": quantity}
        params: dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [item],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": f"{metadata.get('app')}__{metadata.get('tenant')}",
            "metadata": metadata,
            "subscription_data": {"metadata": metadata},
            "billing_address_collection": "auto",
        }
        if trial_days:
            params["subscription_data"]["trial_period_days"] = trial_days
        if promotion_id := await self._promotion_code_id(promo_code):
            params["discounts"] = [{"promotion_code": promotion_id}]
        else:
            params["allow_promotion_codes"] = True
        return to_checkout(await self._api.checkout.sessions.create_async(params))

    async def create_portal_session(self, customer_id: str, return_url: str) -> str:
        session = await self._api.billing_portal.sessions.create_async(
            {"customer": customer_id, "return_url": return_url}
        )
        return plain(session)["url"]

    async def get_subscription(self, subscription_id: str) -> GatewaySubscription | None:
        try:
            return to_subscription(await self._api.subscriptions.retrieve_async(subscription_id))
        except stripe.InvalidRequestError:
            return None

    async def change_subscription_plan(
        self, subscription_id: str, price_id: str, proration_behavior: str
    ) -> GatewaySubscription:
        current = plain(await self._api.subscriptions.retrieve_async(subscription_id))
        updated = await self._api.subscriptions.update_async(subscription_id, {
            "items": [{"id": current["items"]["data"][0]["id"], "price": price_id}],
            "proration_behavior": proration_behavior,
            "cancel_at_period_end": False,
            "payment_behavior": "error_if_incomplete",
        })
        return to_subscription(updated)

    async def cancel_subscription(self, subscription_id: str, at_period_end: bool) -> GatewaySubscription:
        if at_period_end:
            updated = await self._api.subscriptions.update_async(
                subscription_id, {"cancel_at_period_end": True}
            )
        else:
            updated = await self._api.subscriptions.cancel_async(subscription_id)
        return to_subscription(updated)

    async def resume_subscription(self, subscription_id: str) -> GatewaySubscription:
        updated = await self._api.subscriptions.update_async(
            subscription_id, {"cancel_at_period_end": False}
        )
        return to_subscription(updated)

    async def list_invoices(
        self, customer_id: str, limit: int = 20, starting_after: str | None = None
    ) -> tuple[list[Invoice], bool]:
        params: dict[str, Any] = {"customer": customer_id, "limit": limit}
        if starting_after:
            params["starting_after"] = starting_after
        result = await self._api.invoices.list_async(params)
        return [to_invoice(row) for row in result.data], bool(result.has_more)

    async def get_default_payment_method(self, customer_id: str) -> PaymentMethod | None:
        customer = await self._api.customers.retrieve_async(
            customer_id, {"expand": ["invoice_settings.default_payment_method"]}
        )
        method = (plain(customer).get("invoice_settings") or {}).get("default_payment_method")
        if not isinstance(method, dict):
            cards = await self._api.payment_methods.list_async(
                {"customer": customer_id, "type": "card", "limit": 1}
            )
            method = cards.data[0] if cards.data else None
        return to_payment_method(method) if method else None

    def parse_webhook(self, payload: bytes, signature: str | None, secret: str | None = None) -> WebhookEvent:
        """`secret` is the app's own signing secret; the constructor's is only a fallback."""
        try:
            event = stripe.Webhook.construct_event(payload, signature, secret or self._webhook_secret)
        except (stripe.SignatureVerificationError, ValueError) as error:
            raise SignatureError(str(error)) from error
        body = plain(event)
        return WebhookEvent(
            id=body["id"], type=body["type"], created=at(body.get("created")),
            data=plain(body["data"]["object"]),
        )

    def parse_subscription(self, payload: Any) -> GatewaySubscription:
        """Takes either an SDK object or the plain dict a webhook already unpacked."""
        return to_subscription(payload)

    async def _promotion_code_id(self, code: str | None) -> str | None:
        if not code:
            return None
        if code.startswith("promo_"):
            return code
        result = await self._api.promotion_codes.list_async({"code": code, "active": True, "limit": 1})
        return plain(result.data[0])["id"] if result.data else None
