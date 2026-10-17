"""The exact shapes sent to and read back from Stripe, with the client mocked at the boundary.

A fake that accepts anything proved nothing: coupons passed every test while the live API refused
"unknown parameter: coupon", and dict-built fakes passed while every webhook 500'd on StripeObject.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe as sdk
from stripe._stripe_object import StripeObject

from src.models.app import Plan
from src.models.discount import Discount
from src.payment.stripe.gateway import StripeGateway
from src.payment.stripe.parse import plain


def obj(data: dict) -> StripeObject:
    return StripeObject.construct_from(data, "sk_test")


def listing(*rows: dict) -> sdk.ListObject:
    return sdk.ListObject.construct_from({"object": "list", "data": list(rows), "has_more": False}, "sk_test")


@pytest.fixture
def gateway():
    with patch("stripe.StripeClient") as client:
        api = MagicMock()
        api.coupons.create_async = AsyncMock(return_value=obj({"id": "coup_1"}))
        api.coupons.delete_async = AsyncMock()
        api.promotion_codes.create_async = AsyncMock(return_value=obj({"id": "promo_1"}))
        api.products.create_async = AsyncMock(return_value=obj({"id": "prod_1"}))
        api.products.update_async = AsyncMock()
        api.prices.create_async = AsyncMock(return_value=obj({"id": "price_2"}))
        api.prices.update_async = AsyncMock()
        client.return_value.v1 = api  # the gateway reaches the API through `.v1`
        yield StripeGateway("sk_test_x", "whsec_x"), api


async def test_the_promotion_code_nests_the_coupon(gateway):
    stripe, api = gateway
    await stripe.create_coupon("snapback", "LAUNCH99", ["prod_1"], Discount(), "op")
    promo = api.promotion_codes.create_async.await_args.args[0]
    assert promo["promotion"] == {"type": "coupon", "coupon": "coup_1"} and "coupon" not in promo
    assert promo["code"] == "LAUNCH99" and promo["metadata"] == {"app": "snapback", "createdBy": "op"}
    assert api.coupons.create_async.await_args.args[0]["metadata"] == {"app": "snapback"}


async def test_percent_and_amount_terms(gateway):
    stripe, api = gateway
    await stripe.create_coupon("a", "X", ["p"], Discount(value=25, duration="repeating", months=3), "")
    payload = api.coupons.create_async.await_args.args[0]
    terms = (payload["percent_off"], payload["duration"], payload["duration_in_months"])
    assert terms == (25, "repeating", 3)
    assert "amount_off" not in payload
    await stripe.create_coupon("a", "Y", ["p"], Discount(kind="amount", value=1000, currency="eur"), "")
    payload = api.coupons.create_async.await_args.args[0]
    assert (payload["amount_off"], payload["currency"]) == (1000, "eur") and "percent_off" not in payload


async def test_products_scope_the_coupon_and_limits_ride_on_the_code(gateway):
    stripe, api = gateway
    expires = datetime(2026, 12, 31, tzinfo=UTC)
    await stripe.create_coupon("a", "X", ["prod_1"], Discount(max_redemptions=50, expires_at=expires), "")
    coupon = api.coupons.create_async.await_args.args[0]
    promo = api.promotion_codes.create_async.await_args.args[0]
    assert coupon["applies_to"] == {"products": ["prod_1"]} and "max_redemptions" not in coupon
    assert promo["max_redemptions"] == 50 and promo["expires_at"] == int(expires.timestamp())


async def test_a_failed_code_deletes_the_coupon_it_made(gateway):
    stripe, api = gateway
    api.promotion_codes.create_async.side_effect = RuntimeError("unknown parameter")
    with pytest.raises(RuntimeError):
        await stripe.create_coupon("a", "X", ["p"], Discount(), "")
    api.coupons.delete_async.assert_awaited_once_with("coup_1")


async def test_a_webhook_endpoint_returns_its_id_and_secret(gateway):
    stripe, api = gateway
    api.webhook_endpoints.create_async = AsyncMock(return_value=obj({"id": "we_1", "secret": "whsec_1"}))
    assert await stripe.create_webhook_endpoint("https://h/webhooks/stripe/a", ["invoice.paid"]) == (
        "we_1", "whsec_1")
    assert api.webhook_endpoints.create_async.await_args.args[0]["enabled_events"] == ["invoice.paid"]


def test_a_stripe_object_is_not_a_dict_and_plain_flattens_it():
    sdk = obj({"items": {"data": [{"price": {"recurring": {"interval": "month"}}}]}})
    assert not isinstance(sdk, dict)
    with pytest.raises(AttributeError):
        sdk.get("items")  # type: ignore[attr-defined]
    item = plain(sdk)["items"]["data"][0]
    assert isinstance(item, dict) and item["price"]["recurring"]["interval"] == "month"


def test_webhook_and_subscription_parse_from_real_objects(gateway):
    stripe, _ = gateway
    event = obj({"id": "evt_1", "type": "customer.subscription.updated", "created": 1_700_000_000,
                 "data": {"object": {"id": "sub_1", "status": "active", "metadata": {"app": "a"}}}})
    with patch("stripe.Webhook.construct_event", return_value=event):
        parsed = stripe.parse_webhook(b"{}", "sig", "whsec_x")
    assert parsed.id == "evt_1" and isinstance(parsed.data, dict)
    assert parsed.data["metadata"]["app"] == "a"
    sub = stripe.parse_subscription(obj({
        "id": "sub_1", "status": "active", "customer": {"id": "cus_1"}, "metadata": {"tenant": "abc"},
        "items": {"data": [{"quantity": 1, "price": {
            "id": "price_1", "unit_amount": 10, "metadata": {"app": "a", "code": "per-minute"},
            "recurring": {"interval": "month", "meter": "mtr_1"}}}]}}))
    assert (sub.price_id, sub.customer_id, sub.metadata["tenant"]) == ("price_1", "cus_1", "abc")
    assert (sub.plan_code, sub.meter_id) == ("per-minute", "mtr_1")


async def test_invoices_payment_method_checkout_and_price_read_real_objects(gateway):
    stripe, api = gateway
    invoice = {"id": "in_1", "amount_due": 120, "number": "1"}
    api.invoices.list_async = AsyncMock(return_value=listing(invoice))
    invoices, more = await stripe.list_invoices("cus_1")
    assert invoices[0].amount_due == 120 and more is False

    api.customers.retrieve_async = AsyncMock(return_value=obj({"id": "cus_1", "invoice_settings": {
        "default_payment_method": {"id": "pm_1", "card": {"brand": "visa", "last4": "4242"}}}}))
    method = await stripe.get_default_payment_method("cus_1")
    assert method and (method.brand, method.last4) == ("visa", "4242")

    api.checkout.sessions.create_async = AsyncMock(return_value=obj({
        "id": "cs_1", "url": "https://c", "metadata": {"app_id": "a"}, "status": "open"}))
    api.promotion_codes.list_async = AsyncMock(return_value=listing())
    session = await stripe.create_checkout_session("cus_1", "price_1", "https://s", "https://c",
                                                   {"app_id": "a"})
    assert session.metadata == {"app_id": "a"}

    api.customers.search_async = AsyncMock(return_value=listing({"id": "cus_9"}))
    assert await stripe.find_customer("a", "t") == "cus_9"
    api.subscriptions.list_async = AsyncMock(return_value=listing(
        {"id": "sub_old", "status": "canceled", "created": 1},
        {"id": "sub_live", "status": "active", "created": 2}))
    found = await stripe.customer_subscription("cus_9")
    assert found and found.subscription_id == "sub_live"


async def test_a_customer_too_new_for_search_is_found_by_listing(gateway):
    stripe, api = gateway
    api.customers.search_async = AsyncMock(return_value=listing())
    api.customers.list_async = AsyncMock(return_value=listing(
        {"id": "cus_other", "metadata": {"app": "a", "tenant": "x"}},
        {"id": "cus_new", "metadata": {"app": "a", "tenant": "t"}}))
    assert await stripe.find_customer("a", "t") == "cus_new"
    assert api.customers.list_async.await_args.args[0]["created"]["gte"] > 0


PRODUCT = {
    "id": "prod_1", "name": "Per minute", "images": ["https://files.stripe.com/links/x"],
    "metadata": {"app": "soneeva", "code": "per-minute", "status": "active", "unit": "minute",
                 "contact": "false"},
    "marketing_features": [{"name": "Two numbers"}],
    "default_price": {"id": "price_1", "unit_amount": 10, "currency": "usd",
                      "recurring": {"interval": "month", "meter": "mtr_1"}},
}


async def test_plans_are_products_with_their_default_price(gateway):
    stripe, api = gateway
    api.products.list_async = AsyncMock(return_value=listing(PRODUCT, {"id": "prod_x", "metadata": {}}))
    (plan,) = await stripe.list_plans()
    assert (plan.app, plan.code, plan.amount, plan.unit, plan.meter_id) == (
        "soneeva", "per-minute", 10, "minute", "mtr_1")
    assert plan.features == ["Two numbers"] and plan.icon == PRODUCT["images"][0] and plan.active
    assert api.products.list_async.await_args.args[0]["expand"] == ["data.default_price"]


async def test_a_metered_plan_reuses_its_meter_and_a_new_amount_is_a_new_default_price(gateway):
    stripe, api = gateway
    api.billing.meters.list_async = AsyncMock(return_value=listing(
        {"id": "mtr_1", "event_name": "soneeva_per_minute"}))
    plan = Plan(app="soneeva", code="per-minute", name="Per minute", unit="minute", amount=10)
    made = await stripe.create_plan(plan)
    product = api.products.create_async.await_args.args[0]
    assert product["metadata"] == {"app": "soneeva", "code": "per-minute", "status": "active",
                                   "unit": "minute", "contact": "false"} and "description" not in product
    price = api.prices.create_async.await_args.args[0]
    assert price["recurring"] == {"interval": "month", "usage_type": "metered", "meter": "mtr_1"}
    assert price["transform_quantity"] == {"divide_by": 60, "round": "up"}
    assert price["metadata"] == {"app": "soneeva", "code": "per-minute"}
    assert (made.price_id, made.meter_id) == ("price_2", "mtr_1")
    api.prices.create_async.return_value = obj({"id": "price_3"})
    await stripe.update_plan(made, made.model_copy(update={"amount": 12}))
    api.prices.update_async.assert_awaited_once_with("price_2", {"active": False})
    assert api.products.update_async.await_args.args == ("prod_1", {"default_price": "price_3"})
    api.billing.meters.create_async.assert_not_called()


async def test_a_repeated_meter_event_is_not_billed_twice(gateway):
    stripe, api = gateway
    api.billing.meters.retrieve_async = AsyncMock(return_value=obj({"id": "mtr_1", "event_name": "e"}))
    api.billing.meter_events.create_async = AsyncMock()
    assert await stripe.record_usage("mtr_1", "cus_1", 61, "call-1") is True
    sent = api.billing.meter_events.create_async.await_args.args[0]
    assert sent == {"event_name": "e", "identifier": "call-1",
                    "payload": {"stripe_customer_id": "cus_1", "value": "61"}}
    api.billing.meter_events.create_async.side_effect = sdk.InvalidRequestError(
        "An event with identifier call-1 already exists", "identifier", code="resource_already_exists")
    assert await stripe.record_usage("mtr_1", "cus_1", 61, "call-1") is False


async def test_coupons_are_read_back_from_promotion_codes(gateway):
    stripe, api = gateway
    api.promotion_codes.list_async = AsyncMock(return_value=listing({
        "id": "promo_1", "code": "SPRING2026", "active": True, "max_redemptions": 50, "times_redeemed": 2,
        "metadata": {"app": "soneeva", "createdBy": "op"},
        "promotion": {"type": "coupon", "coupon": {"id": "coup_1", "amount_off": 1000, "currency": "eur",
                                                   "duration": "repeating", "duration_in_months": 3}}},
        {"id": "promo_2", "code": "OTHERAPP1", "active": True, "metadata": {"app": "other"},
         "promotion": {"coupon": {"id": "coup_2", "percent_off": 10, "duration": "once"}}}))
    (coupon,) = await stripe.list_coupons("soneeva")
    assert (coupon.code, coupon.coupon_id, coupon.times_redeemed, coupon.created_by) == (
        "SPRING2026", "coup_1", 2, "op")
    terms = coupon.discount
    assert (terms.kind, terms.value, terms.months, terms.max_redemptions) == ("amount", 1000, 3, 50)
