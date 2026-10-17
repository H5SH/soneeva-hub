"""Plans (products and prices), meters, coupons and webhook endpoints."""

import io
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import stripe

from src.models.app import UNIT_SECONDS, Plan
from src.models.discount import Coupon, Discount, DiscountKind
from src.payment.stripe.parse import plain, to_coupon, to_plan

PROMOTION_EXPAND = ["data.promotion.coupon"]


def product_fields(plan: Plan, create: bool = False) -> dict[str, Any]:
    """On an update an empty value unsets the field; Stripe refuses empty values on a create."""
    meta = {"app": plan.app, "code": plan.code, "status": plan.status, "unit": plan.unit,
            "contact": "true" if plan.contact else "false", "trialDays": str(plan.trial_days or "")}
    fields = {"name": plan.name, "description": plan.description or "", "metadata": meta,
              "marketing_features": [{"name": f} for f in plan.features] or ""}
    if create:
        meta = {k: v for k, v in meta.items() if v}
        fields = {k: v for k, v in fields.items() if v} | {"metadata": meta}
    return fields


def same_price(a: Plan, b: Plan) -> bool:
    return (a.amount, a.currency, a.unit, a.interval) == (b.amount, b.currency, b.unit, b.interval)


def meter_event(plan: Plan) -> str:
    """One meter per plan; every price the plan ever has bills against it."""
    return re.sub(r"[^a-z0-9_]", "_", f"{plan.app}_{plan.code}".lower())


class StripeCatalog:
    _api: Any
    _meter_events: dict[str, str]

    async def list_plans(self) -> list[Plan]:
        page = await self._api.products.list_async(
            {"active": True, "limit": 100, "expand": ["data.default_price"]})
        plans = [to_plan(p) async for p in page.auto_paging_iter()]
        return [p for p in plans if p.app]

    async def create_plan(self, plan: Plan) -> Plan:
        product = plain(await self._api.products.create_async(product_fields(plan, create=True)))
        plan = plan.model_copy(update={"product_id": product["id"]})
        return await self._reprice(plan) if plan.amount else plan

    async def update_plan(self, current: Plan, wanted: Plan) -> Plan:
        wanted = wanted.model_copy(update={"product_id": current.product_id, "price_id": current.price_id,
                                           "meter_id": current.meter_id, "icon": current.icon})
        await self._api.products.update_async(current.product_id, product_fields(wanted))
        if same_price(current, wanted):
            return wanted
        if wanted.amount:
            wanted = await self._reprice(wanted)
        else:
            await self._api.products.update_async(current.product_id, {"default_price": ""})
            wanted = wanted.model_copy(update={"price_id": None, "meter_id": None})
        if current.price_id:
            await self._api.prices.update_async(current.price_id, {"active": False})
        return wanted

    async def _reprice(self, plan: Plan) -> Plan:
        """A new price (metered against the plan's meter when the unit is not a month), made default."""
        params: dict[str, Any] = {
            "product": plan.product_id, "unit_amount": plan.amount, "currency": plan.currency,
            "recurring": {"interval": plan.interval}, "metadata": {"app": plan.app, "code": plan.code},
        }
        meter_id = None
        if plan.metered:
            meter_id = await self._meter(plan)
            params["recurring"] = {"interval": "month", "usage_type": "metered", "meter": meter_id}
            params["transform_quantity"] = {"divide_by": UNIT_SECONDS[plan.unit], "round": "up"}
        price = plain(await self._api.prices.create_async(params))
        await self._api.products.update_async(plan.product_id, {"default_price": price["id"]})
        return plan.model_copy(update={"price_id": price["id"], "meter_id": meter_id})

    async def _meter(self, plan: Plan) -> str:
        event = meter_event(plan)
        page = await self._api.billing.meters.list_async({"status": "active", "limit": 100})
        async for meter in page.auto_paging_iter():
            if meter.event_name == event:
                return meter.id
        meter = await self._api.billing.meters.create_async({
            "display_name": f"{plan.app} {plan.code}", "event_name": event,
            "default_aggregation": {"formula": "sum"},
            "customer_mapping": {"type": "by_id", "event_payload_key": "stripe_customer_id"},
            "value_settings": {"event_payload_key": "value"},
        })
        return meter.id

    async def archive_plan(self, plan: Plan) -> None:
        """The product first: Stripe refuses to archive a price that is still a default price."""
        await self._api.products.update_async(plan.product_id, {"active": False, "default_price": ""})
        if plan.price_id:
            await self._api.prices.update_async(plan.price_id, {"active": False})

    async def set_plan_icon(self, plan: Plan, data: bytes | None, content_type: str = "") -> str | None:
        """Stripe-hosted: a `business_logo` file with a public file link, as the product's image."""
        if data is None:
            await self._api.products.update_async(plan.product_id, {"images": ""})
            return None
        upload = io.BytesIO(data)
        upload.name = f"{plan.code}.{content_type.split('/')[-1]}"
        made = plain(await self._api.files.create_async(
            {"purpose": "business_logo", "file": upload, "file_link_data": {"create": True}}))
        url = made["links"]["data"][0]["url"]
        await self._api.products.update_async(plan.product_id, {"images": [url]})
        return url

    async def record_usage(self, meter_id: str, customer_id: str, value: int, identifier: str) -> bool:
        if meter_id not in self._meter_events:
            meter = await self._api.billing.meters.retrieve_async(meter_id)
            self._meter_events[meter_id] = meter.event_name
        try:
            await self._api.billing.meter_events.create_async({
                "event_name": self._meter_events[meter_id], "identifier": identifier,
                "payload": {"stripe_customer_id": customer_id, "value": str(value)},
            })
        except stripe.InvalidRequestError as error:
            if error.code == "resource_already_exists" or "identifier" in str(error).lower():
                return False
            raise
        return True

    async def usage_days(self, meter_id: str, customer_id: str, days: int) -> list[dict[str, Any]]:
        end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        page = await self._api.billing.meters.event_summaries.list_async(meter_id, {
            "customer": customer_id, "start_time": int((end - timedelta(days=days)).timestamp()),
            "end_time": int(end.timestamp()), "value_grouping_window": "day", "limit": 100,
        })
        rows = [{"day": datetime.fromtimestamp(s.start_time, UTC).date().isoformat(),
                 "seconds": int(s.aggregated_value)} async for s in page.auto_paging_iter()]
        return sorted(rows, key=lambda r: r["day"], reverse=True)

    async def list_coupons(self, app_id: str) -> list[Coupon]:
        page = await self._api.promotion_codes.list_async(
            {"active": True, "limit": 100, "expand": PROMOTION_EXPAND})
        coupons = [to_coupon(p) async for p in page.auto_paging_iter()]
        return [c for c in coupons if c.app_id == app_id]

    async def find_coupon(self, code: str) -> Coupon | None:
        found = await self._api.promotion_codes.list_async(
            {"code": code, "active": True, "limit": 1, "expand": PROMOTION_EXPAND})
        return to_coupon(found.data[0]) if found.data else None

    async def create_coupon(
        self, app_id: str, code: str, product_ids: list[str], discount: Discount, created_by: str
    ) -> Coupon:
        """`applies_to` keeps one app's code off another's checkout. A failed promotion code deletes
        the coupon, which would otherwise be orphaned."""
        payload: dict[str, Any] = {"duration": discount.duration.value, "name": f"{app_id}: {code}",
                                   "metadata": {"app": app_id}, "applies_to": {"products": product_ids}}
        if discount.kind is DiscountKind.PERCENT:
            payload["percent_off"] = discount.value
        else:
            payload |= {"amount_off": int(discount.value), "currency": discount.currency.lower()}
        if discount.months:
            payload["duration_in_months"] = discount.months
        coupon_id = plain(await self._api.coupons.create_async(payload))["id"]
        promo: dict[str, Any] = {"promotion": {"type": "coupon", "coupon": coupon_id}, "code": code,
                                 "metadata": {"app": app_id, "createdBy": created_by}}
        if discount.max_redemptions:
            promo["max_redemptions"] = discount.max_redemptions
        if discount.expires_at:
            promo["expires_at"] = int(discount.expires_at.timestamp())
        try:
            made = plain(await self._api.promotion_codes.create_async(promo))
        except Exception:
            await self._api.coupons.delete_async(coupon_id)
            raise
        return Coupon(code=code, app_id=app_id, coupon_id=coupon_id, promotion_code_id=made["id"],
                      created_by=created_by, discount=discount)

    async def delete_coupon(self, coupon: Coupon) -> None:
        """Promotion codes cannot be deleted, only deactivated; the coupon goes with it."""
        await self._api.promotion_codes.update_async(coupon.promotion_code_id, {"active": False})
        await self._api.coupons.delete_async(coupon.coupon_id)

    async def create_webhook_endpoint(self, url: str, events: list[str]) -> tuple[str, str]:
        created = await self._api.webhook_endpoints.create_async({"url": url, "enabled_events": events})
        endpoint = plain(created)
        if not endpoint.get("secret"):
            raise RuntimeError("Stripe created the endpoint but returned no signing secret")
        return endpoint["id"], endpoint["secret"]
