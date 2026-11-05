"""Paying through Shopify: the plan, its usage charges, and what an uninstall ends."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.models.admin import Scope
from src.models.identity import Identity
from src.stores import StoreBillingError
from tests.setup.platform import business, subscribe
from tests.setup.stores import PLAN, STORE, STORE_PATH
from tests.test_handoff_billing import SUCCESS


def test_a_shopify_plan_is_the_business_plan_everywhere(client, console, services, signed_up):
    biz, url = signed_up["id"], f"/v1/businesses/{signed_up['id']}"
    connected(services, signed_up)
    done = client.put(f"{url}/plan", json=PLAN, headers=console(biz))
    assert done.status_code == 200, done.text
    active = {"code": "per-minute", "status": "active", "active": True}
    assert done.json()["plan"] == active
    assert client.get(url, headers=console(biz)).json()["plan"] == active
    assert client.get("/v1/businesses", headers=console()).json()["businesses"][0]["plan"] == active
    assert "storePlan" not in done.json() and "uninstalled" not in done.json()
    status = client.get(f"{url}/plan", headers=console(biz)).json()
    assert (status["active"], status["plan"]) == (True, "per-minute")
    uid = signed_up["ownerUid"]
    listed = client.get(f"{url}/plans", headers=console(biz, uid)).json()
    assert listed["subscription"]["active"] and listed["plans"][0]["current"]
    assert client.post(f"{url}/checkout", json=SUCCESS, headers=console(biz, uid)).status_code == 409
    assert services.business_dbs[signed_up["dbId"]].rows[("checklist", "subscribed")]["state"] == "done"
    assert f"voice-{biz}" in services.voice._vault.versions


def test_a_cancelled_shopify_plan_stops_service_and_numbers(client, console, services, signed_up):
    biz, url = signed_up["id"], f"/v1/businesses/{signed_up['id']}"
    connected(services, signed_up)
    client.put(f"{url}/plan", json=PLAN, headers=console(biz))
    cancelled = {k: v for k, v in PLAN.items() if k != "usageLineItemId"} | {"status": "cancelled"}
    assert client.put(f"{url}/plan", json=cancelled, headers=console(biz)).json()["plan"]["active"] is False
    assert client.get(url, headers=console(biz)).json()["plan"]["active"] is False
    assert client.get(f"{url}/plan", headers=console(biz)).json()["active"] is False
    assert client.get(f"{url}/numbers?country=NL", headers=console(biz)).status_code == 402
    stored = services.db.rows[("businesses", biz)]["storePlan"]
    assert stored["usageLineItemId"] == PLAN["usageLineItemId"]


def test_a_shopify_plan_needs_a_shopify_store_and_a_known_plan(client, console, services, signed_up):
    biz = business(services)
    assert client.put(f"/v1/businesses/{biz}/plan", json=PLAN, headers=console(biz)).status_code == 409
    theirs = signed_up["id"]
    url = f"/v1/businesses/{theirs}/plan"
    assert client.put(url, json=PLAN | {"code": "nope"}, headers=console(theirs)).status_code == 422
    assert client.put(url, json=PLAN | {"source": "bigcommerce"}, headers=console(theirs)).status_code == 422


def test_a_stripe_business_still_reads_stripe(client, console, services):
    biz = business(services, subscribed=False)
    subscribe(services, biz)
    assert client.get(f"/v1/businesses/{biz}", headers=console(biz)).json()["plan"]["status"] == "active"


def connected(services, signed_up) -> None:
    """The token the console saved."""
    services.business_dbs[signed_up["dbId"]].rows[("connections", "shopify")] = {
        "storeId": STORE["storeId"], "token": "sealed"}


def report(client, bearer, biz, call="call-1", seconds=95):
    headers = bearer("soneeva-voice", [Scope.HUB_USAGE_WRITE], sub=biz)
    return client.post("/v1/usage", json={"callId": call, "seconds": seconds}, headers=headers)


@pytest.fixture
def paying(client, console, services, signed_up):
    """Paying through Shopify, with the token the console saved."""
    biz = signed_up["id"]
    connected(services, signed_up)
    assert client.put(f"/v1/businesses/{biz}/plan", json=PLAN, headers=console(biz)).status_code == 200
    return biz


def test_a_shopify_business_pays_for_its_calls_through_shopify(client, bearer, services, paying):
    assert report(client, bearer, paying).json() == {"status": "recorded", "billed": True}
    assert report(client, bearer, paying).json()["billed"] is True  # the same call: Shopify's key dedupes it
    assert services.store_billing.charges == {
        "call-1": (STORE["storeId"], "sealed", PLAN["usageLineItemId"], Decimal("0.16"), "USD")}
    assert services.payment.live.usage == {}


def test_nothing_is_charged_once_the_plan_ended_or_for_no_seconds(client, bearer, console, services, paying):
    assert report(client, bearer, paying, seconds=0).json()["billed"] is False
    client.post(f"/v1/businesses/{paying}/disable", headers=console(paying))
    assert report(client, bearer, paying).json()["billed"] is False
    assert services.store_billing.charges == {}


def test_a_charge_that_fails_is_reported_again(client, bearer, services, paying):
    async def refused(*_):
        raise StoreBillingError("Shopify: over the cap")

    services.store_billing.charge = refused
    assert report(client, bearer, paying).status_code == 502


def test_an_active_plan_needs_its_usage_line_item(client, console, signed_up):
    biz = signed_up["id"]
    without = {k: v for k, v in PLAN.items() if k != "usageLineItemId"}
    assert client.put(f"/v1/businesses/{biz}/plan", json=without, headers=console(biz)).status_code == 422


def test_uninstall_ends_the_shopify_plan_and_service(client, console, services, paying):
    url = f"/v1/businesses/{paying}"
    client.post(f"{url}/disable", headers=console(paying))
    assert client.get(f"{url}/plan", headers=console(paying)).json()["active"] is False
    assert services.db.rows[("businesses", paying)]["storePlan"]["status"] == "cancelled"
    client.post(f"{url}/enable", headers=console(paying))
    assert client.get(url, headers=console(paying)).json()["plan"]["active"] is False  # a reinstall pays anew
    again = PLAN | {"subscriptionId": "gid://shopify/AppSubscription/2", "at": datetime.now(UTC).isoformat()}
    assert client.put(f"{url}/plan", json=again, headers=console(paying)).json()["plan"]["active"] is True


def test_uninstall_stops_a_stripe_business_being_served(client, console, services):
    biz = business(services, subscribed=True)
    services.db.rows[("stores", "bigcommerce:abc123")] = {"businessId": biz, "platform": "bigcommerce"}
    client.post(f"/v1/businesses/{biz}/disable", headers=console(biz))
    assert client.get(f"/v1/businesses/{biz}/plan", headers=console(biz)).json()["active"] is False
    assert client.get(f"/v1/businesses/{biz}/numbers?country=NL", headers=console(biz)).status_code == 402
    client.post(f"/v1/businesses/{biz}/enable", headers=console(biz))
    assert client.get(f"/v1/businesses/{biz}/plan", headers=console(biz)).json()["active"] is True


def test_late_word_of_a_replaced_subscription_changes_nothing(client, console, paying):
    url = f"/v1/businesses/{paying}/plan"
    for status in ("cancelled", "pending", "declined"):
        old = PLAN | {"subscriptionId": "gid://old", "status": status}
        assert client.put(url, json=old, headers=console(paying)).json()["plan"]["active"] is True


def test_an_older_active_never_replaces_the_current_subscription(client, console, services, paying):
    url = f"/v1/businesses/{paying}/plan"
    earlier = (datetime.fromisoformat(PLAN["at"]) - timedelta(hours=1)).isoformat()
    old = PLAN | {"subscriptionId": "gid://old", "at": earlier}
    client.put(url, json=old, headers=console(paying))
    assert services.db.rows[("businesses", paying)]["storePlan"]["subscriptionId"] == PLAN["subscriptionId"]


def test_a_late_active_doesnt_revive_a_plan_after_an_uninstall(client, console, paying):
    url = f"/v1/businesses/{paying}"
    client.post(f"{url}/disable", headers=console(paying))
    late = PLAN | {"at": datetime.now(UTC).isoformat()}
    assert client.put(f"{url}/plan", json=late, headers=console(paying)).status_code == 200
    assert client.get(url, headers=console(paying)).json()["plan"]["active"] is False


def test_going_active_needs_a_token_to_charge_with(client, console, signed_up):
    biz = signed_up["id"]
    assert client.put(f"/v1/businesses/{biz}/plan", json=PLAN, headers=console(biz)).status_code == 409


def test_charges_failing_past_the_grace_stop_service_until_the_plan_is_fixed(
    client, bearer, console, services, paying,
):
    async def refused(*_):
        raise StoreBillingError("Shopify: over the cap")

    real, services.store_billing.charge = services.store_billing.charge, refused
    plan_url = f"/v1/businesses/{paying}/plan"
    assert report(client, bearer, paying).status_code == 502
    row = services.db.rows[("businesses", paying)]
    first = row["chargeFailedAt"]
    assert client.get(plan_url, headers=console(paying)).json()["active"] is True  # still within the grace
    assert report(client, bearer, paying, call="call-2").status_code == 502
    assert row["chargeFailedAt"] == first
    row["chargeFailedAt"] = datetime.now(UTC) - timedelta(days=2)
    assert client.get(plan_url, headers=console(paying)).json()["active"] is False
    services.store_billing.charge = real
    fixed = PLAN | {"at": datetime.now(UTC).isoformat()}
    assert client.put(plan_url, json=fixed, headers=console(paying)).status_code == 200  # the cap was raised
    assert client.get(plan_url, headers=console(paying)).json()["active"] is True


def test_a_charge_that_succeeds_clears_the_failures(client, bearer, services, paying):
    services.db.rows[("businesses", paying)]["chargeFailedAt"] = datetime.now(UTC)
    assert report(client, bearer, paying).json()["billed"] is True
    assert services.db.rows[("businesses", paying)]["chargeFailedAt"] is None


def test_no_token_row_is_a_failure_too(client, bearer, services, signed_up, paying):
    del services.business_dbs[signed_up["dbId"]].rows[("connections", "shopify")]
    assert report(client, bearer, paying).status_code == 502
    assert services.db.rows[("businesses", paying)]["chargeFailedAt"]


def test_deleting_on_the_platform_stops_service(client, console, paying):
    url = f"/v1/businesses/{paying}"
    assert client.patch(url, json={"status": "deleted"}, headers=console()).status_code == 200
    assert client.get(f"{url}/plan", headers=console(paying)).json()["active"] is False


def test_an_uninstall_after_a_platform_delete_still_ends_the_plan(client, console, services, paying):
    url = f"/v1/businesses/{paying}"
    client.patch(url, json={"status": "deleted"}, headers=console())
    client.post(f"{url}/disable", headers=console(paying))
    row = services.db.rows[("businesses", paying)]
    assert row["storePlan"]["status"] == "cancelled" and row["uninstalled"]["at"]
    assert client.post(f"{url}/enable", headers=console(paying)).status_code == 409


def test_a_platform_delete_after_an_uninstall_keeps_the_date_only(client, console, services, paying):
    url = f"/v1/businesses/{paying}"
    client.post(f"{url}/disable", headers=console(paying))
    at = services.db.rows[("businesses", paying)]["uninstalled"]["at"]
    assert client.patch(url, json={"status": "deleted"}, headers=console()).status_code == 200
    assert services.db.rows[("businesses", paying)]["uninstalled"] == {"status": None, "at": at}
    assert client.get(f"{url}/plan", headers=console(paying)).json()["active"] is False
    assert client.post(f"{url}/enable", headers=console(paying)).status_code == 409


def test_a_retried_uninstall_doesnt_touch_a_reinstalled_store(client, console, services, paying):
    url, started = f"/v1/businesses/{paying}", datetime.now(UTC).isoformat()
    client.post(f"{url}/disable", json={"at": started}, headers=console(paying))
    client.post(f"{url}/enable", headers=console(paying))
    new = PLAN | {"subscriptionId": "gid://new", "at": datetime.now(UTC).isoformat()}
    client.put(f"{url}/plan", json=new, headers=console(paying))
    assert client.post(f"{url}/disable", json={"at": started}, headers=console(paying)).status_code == 200
    row = services.db.rows[("businesses", paying)]
    assert row["status"] != "deleted" and row["storePlan"]["status"] == "active"
    later = {"at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}
    assert client.post(f"{url}/disable", json=later, headers=console(paying)).json()["status"] == "deleted"


def test_disconnecting_the_store_ends_the_plan_paid_through_it(client, console, signed_up, paying):
    url = f"/v1/businesses/{paying}"
    assert client.delete(STORE_PATH, headers=console(paying, signed_up["ownerUid"])).status_code == 204
    assert client.get(f"{url}/plan", headers=console(paying)).json()["active"] is False


def test_a_business_on_stripe_is_not_charged_through_shopify_too(client, console, services):
    biz = business(services, subscribed=True)
    services.db.rows[("stores", "shopify:rosa.myshopify.com")] = {"businessId": biz, "platform": "shopify"}
    assert client.put(f"/v1/businesses/{biz}/plan", json=PLAN, headers=console(biz)).status_code == 409


def test_the_payment_admin_and_listings_see_a_shopify_plan(services, paying):
    admin = Identity(uid="a", role="superadmin", payment_scopes=list(Scope))
    (_, record), = [r for r in asyncio.run(services.customers.every(admin)) if r[0].id == paying]
    assert record.is_active and record.plan_code == "per-minute"
    assert asyncio.run(services.customers.one(admin, paying))[1].is_active
