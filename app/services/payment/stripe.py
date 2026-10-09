import time
from datetime import UTC, datetime, timedelta
from typing import Any

import stripe
from stripe.checkout import Session

from app.models.payment import CheckoutSession, Invoice, PaymentMethod, Price, WebhookEvent
from app.models.subscription import GatewaySubscription, SubscriptionStatus
from app.services.payment.base import PaymentGateway, SignatureError

PRICE_CACHE_TTL_SECONDS = 300
LIVE_STATUSES = ("trialing", "active", "past_due", "unpaid", "paused", "incomplete")


class StripeGateway(PaymentGateway):
    """Stripe implementation of the payment contract."""

    def __init__(self, api_key: str, webhook_secret: str) -> None:
        self._api = stripe.StripeClient(api_key).v1
        self._webhook_secret = webhook_secret
        self._price_cache: dict[str, tuple[float, Price]] = {}

    async def create_customer(
        self, metadata: dict[str, str], name: str | None = None
    ) -> str:
        customer = await self._api.customers.create_async(
            {"metadata": metadata, "name": name}
        )
        return customer.id

    @staticmethod
    def _quote(value: str) -> str:
        """Escape a value for Stripe's search query language.

        These come from a path segment and a signed claim, so they are not free-form user input,
        but an unescaped quote would still silently change the query's meaning.
        """
        return value.replace("\\", "\\\\").replace("'", "\\'")

    async def find_customer(self, app_id: str, store_hash: str) -> str | None:
        query = (
            f"metadata['app_id']:'{self._quote(app_id)}' "
            f"AND metadata['store_hash']:'{self._quote(store_hash)}'"
        )
        result = await self._api.customers.search_async({"query": query, "limit": 1})
        return result.data[0].id if result.data else None

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
        params: dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": quantity}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": f"{metadata.get('app_id')}__{metadata.get('store_hash')}",
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
        return self._to_checkout(await self._api.checkout.sessions.create_async(params))

    async def get_checkout_session(self, session_id: str) -> CheckoutSession | None:
        try:
            session = await self._api.checkout.sessions.retrieve_async(session_id)
        except stripe.InvalidRequestError:
            return None
        return self._to_checkout(session)

    async def create_portal_session(self, customer_id: str, return_url: str) -> str:
        session = await self._api.billing_portal.sessions.create_async(
            {"customer": customer_id, "return_url": return_url}
        )
        return session.url

    async def get_subscription(self, subscription_id: str) -> GatewaySubscription | None:
        try:
            subscription = await self._api.subscriptions.retrieve_async(subscription_id)
        except stripe.InvalidRequestError:
            return None
        return self.parse_subscription(subscription)

    async def find_subscription(self, customer_id: str) -> GatewaySubscription | None:
        result = await self._api.subscriptions.list_async(
            {"customer": customer_id, "status": "all", "limit": 10}
        )
        candidates = [s for s in result.data if s.status in LIVE_STATUSES] or list(result.data)
        if not candidates:
            return None
        return self.parse_subscription(max(candidates, key=lambda s: s.created))

    async def change_subscription_plan(
        self, subscription_id: str, price_id: str, proration_behavior: str
    ) -> GatewaySubscription:
        current = await self._api.subscriptions.retrieve_async(subscription_id)
        updated = await self._api.subscriptions.update_async(
            subscription_id,
            {
                "items": [{"id": current["items"]["data"][0]["id"], "price": price_id}],
                "proration_behavior": proration_behavior,
                "cancel_at_period_end": False,
                "payment_behavior": "error_if_incomplete",
            },
        )
        return self.parse_subscription(updated)

    async def cancel_subscription(
        self, subscription_id: str, at_period_end: bool
    ) -> GatewaySubscription:
        if at_period_end:
            updated = await self._api.subscriptions.update_async(
                subscription_id, {"cancel_at_period_end": True}
            )
        else:
            updated = await self._api.subscriptions.cancel_async(subscription_id)
        return self.parse_subscription(updated)

    async def resume_subscription(self, subscription_id: str) -> GatewaySubscription:
        updated = await self._api.subscriptions.update_async(
            subscription_id, {"cancel_at_period_end": False}
        )
        return self.parse_subscription(updated)

    async def list_invoices(
        self, customer_id: str, limit: int = 20, starting_after: str | None = None
    ) -> tuple[list[Invoice], bool]:
        params: dict[str, Any] = {"customer": customer_id, "limit": limit}
        if starting_after:
            params["starting_after"] = starting_after
        result = await self._api.invoices.list_async(params)
        invoices = [
            Invoice(
                id=row["id"],
                number=row.get("number"),
                status=row.get("status"),
                amount_due=row.get("amount_due") or 0,
                amount_paid=row.get("amount_paid") or 0,
                currency=row.get("currency"),
                created=self._at(row.get("created")),
                period_start=self._at(row.get("period_start")),
                period_end=self._at(row.get("period_end")),
                hosted_invoice_url=row.get("hosted_invoice_url"),
                invoice_pdf=row.get("invoice_pdf"),
            )
            for row in result.data
        ]
        return invoices, bool(result.has_more)

    async def get_default_payment_method(self, customer_id: str) -> PaymentMethod | None:
        customer = await self._api.customers.retrieve_async(
            customer_id, {"expand": ["invoice_settings.default_payment_method"]}
        )
        method = (customer.get("invoice_settings") or {}).get("default_payment_method")
        if not method:
            cards = await self._api.payment_methods.list_async(
                {"customer": customer_id, "type": "card", "limit": 1}
            )
            method = cards.data[0] if cards.data else None
        if not method:
            return None
        card = method.get("card") or {}
        return PaymentMethod(
            id=method.get("id"),
            brand=card.get("brand"),
            last4=card.get("last4"),
            exp_month=card.get("exp_month"),
            exp_year=card.get("exp_year"),
            wallet=(card.get("wallet") or {}).get("type"),
        )

    async def create_price(
        self,
        app_id: str,
        plan_code: str,
        name: str,
        amount: int,
        currency: str,
        interval: str,
    ) -> tuple[str, str]:
        """A product per plan, so a coupon can later be restricted to one app's products."""
        product = await self._api.products.create_async(
            {"name": name, "metadata": {"app_id": app_id, "plan_code": plan_code}}
        )
        price = await self._api.prices.create_async(
            {
                "product": product.id,
                "unit_amount": amount,
                "currency": currency.lower(),
                "recurring": {"interval": interval},
                "metadata": {"app_id": app_id, "plan_code": plan_code},
            }
        )
        return product.id, price.id

    async def revenue_by_day(self, days: int) -> list[tuple[str, int]]:
        """Gross succeeded charges, net of refunds, grouped by day.

        Capped rather than auto-paginated: this runs on a dashboard load, and an account
        with a long history should not turn that into a hundred sequential API calls.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        totals: dict[str, int] = {
            (since + timedelta(days=offset)).date().isoformat(): 0 for offset in range(days + 1)
        }

        starting_after: str | None = None
        for _ in range(5):  # 5 x 100 charges is plenty for a 30-day window
            params: dict[str, Any] = {
                "created": {"gte": int(since.timestamp())},
                "limit": 100,
            }
            if starting_after:
                params["starting_after"] = starting_after
            page = await self._api.charges.list_async(params)
            rows = page.data or []
            for charge in rows:
                if charge.status != "succeeded":
                    continue
                day = datetime.fromtimestamp(charge.created, UTC).date().isoformat()
                if day in totals:
                    totals[day] += charge.amount - (charge.amount_refunded or 0)
            if not page.has_more or not rows:
                break
            starting_after = rows[-1].id

        return sorted(totals.items())

    async def can_write_products(self) -> tuple[bool, str]:
        """Checked before the plan step, so a restricted key fails here rather than midway."""
        try:
            await self._api.products.list_async({"limit": 1})
        except stripe.PermissionError as error:
            return False, str(error)
        except stripe.AuthenticationError as error:
            return False, f"Stripe rejected the key: {error}"
        except Exception as error:  # noqa: BLE001 - surfaced to the operator as-is
            return False, str(error)
        return True, ""

    async def create_full_discount(
        self, code: str, app_id: str, product_ids: list[str], max_redemptions: int | None
    ) -> tuple[str, str]:
        """100% off, forever, so the customer gets a real subscription at zero and nothing
        downstream needs to know a coupon was involved.

        Promotion codes are global to the Stripe account, so `applies_to` is what stops one
        app's code working on another's checkout.
        """
        payload: dict[str, Any] = {
            "percent_off": 100,
            "duration": "forever",
            "name": f"{app_id}: {code}",
            "metadata": {"app_id": app_id},
        }
        if product_ids:
            payload["applies_to"] = {"products": product_ids}
        coupon = await self._api.coupons.create_async(payload)

        promo: dict[str, Any] = {
            "coupon": coupon.id,
            "code": code,
            "metadata": {"app_id": app_id},
        }
        if max_redemptions:
            promo["max_redemptions"] = max_redemptions
        promotion = await self._api.promotion_codes.create_async(promo)
        return coupon.id, promotion.id

    async def delete_promotion_code(self, promotion_code_id: str) -> None:
        # Stripe has no delete for promotion codes; deactivating is the documented equivalent.
        await self._api.promotion_codes.update_async(promotion_code_id, {"active": False})

    async def get_price(self, price_id: str) -> Price | None:
        cached = self._price_cache.get(price_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        row = await self._api.prices.retrieve_async(price_id)
        recurring = row.recurring
        
        price = Price(
            id=row.id,
            amount=row.unit_amount,
            currency=row.currency
        )

        if recurring:
            price.interval = recurring.interval
            price.interval_count = recurring.interval_count
            price.trial_days = recurring.trial_period_days
                    
        self._price_cache[price_id] = (time.monotonic() + PRICE_CACHE_TTL_SECONDS, price)
        return price

    def parse_webhook(
        self, payload: bytes, signature: str | None, secret: str | None = None
    ) -> WebhookEvent:
        """`secret` is the app's own signing secret; the constructor's is only a fallback.

        One endpoint exists per app, so a single global secret would force every app onto the
        same Stripe account.
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, secret or self._webhook_secret
            )
        except (stripe.SignatureVerificationError, ValueError) as error:
            raise SignatureError(str(error)) from error
        return WebhookEvent(
            id=event["id"],
            type=event["type"],
            created=self._at(event.get("created")),
            data=dict(event["data"]["object"]),
        )

    def parse_subscription(self, payload: dict[str, Any]) -> GatewaySubscription:
        item = ((payload.get("items") or {}).get("data") or [{}])[0]
        price = item.get("price") or {}
        recurring = price.get("recurring") or {}
        customer = payload.get("customer")
        invoice = payload.get("latest_invoice")
        return GatewaySubscription(
            subscription_id=payload["id"],
            customer_id=customer if isinstance(customer, str) else (customer or {}).get("id"),
            status=SubscriptionStatus(payload["status"]),
            price_id=price.get("id"),
            quantity=item.get("quantity") or 1,
            amount=price.get("unit_amount"),
            currency=price.get("currency") or payload.get("currency"),
            interval=recurring.get("interval"),
            current_period_start=self._at(
                item.get("current_period_start") or payload.get("current_period_start")
            ),
            current_period_end=self._at(
                item.get("current_period_end") or payload.get("current_period_end")
            ),
            trial_end=self._at(payload.get("trial_end")),
            cancel_at_period_end=bool(payload.get("cancel_at_period_end")),
            canceled_at=self._at(payload.get("canceled_at")),
            latest_invoice_id=invoice if isinstance(invoice, str) else (invoice or {}).get("id"),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _to_checkout(self, payload: Session) -> CheckoutSession:
        # None, not 'unknown': complete_checkout tests `if not session.subscription_id`, and a
        # truthy sentinel made every hosted checkout return land as "pending" instead of success.
        customer = payload.customer
        customer_id = customer if isinstance(customer, str) else None

        subscription = payload.subscription
        subscription_id = subscription if isinstance(subscription, str) else None

        return CheckoutSession(
            id=payload.id,
            url=payload.url,
            customer_id=customer_id,
            subscription_id=subscription_id,
            status=payload.status,
            payment_status=payload.payment_status,
            metadata=payload.metadata.to_dict() # type: ignore
        )

    async def _promotion_code_id(self, code: str | None) -> str | None:
        if not code:
            return None
        if code.startswith("promo_"):
            return code
        result = await self._api.promotion_codes.list_async(
            {"code": code, "active": True, "limit": 1}
        )
        return result.data[0].id if result.data else None

    @staticmethod
    def _at(timestamp: int | None) -> datetime | None:
        return datetime.fromtimestamp(timestamp, UTC) if timestamp else None
