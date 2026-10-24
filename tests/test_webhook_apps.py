"""Per-app webhooks: provisioning, the write-only secret, and events that name another app."""

import asyncio
import json

from src.services.webhook import WEBHOOK_EVENTS
from tests.setup.platform import business
from tests.setup.staff import staff

APPS = "/v1/payment/apps"


def test_an_event_naming_another_app_is_not_applied_here(client, services):
    """Stripe sends every app's events to every endpoint on the account; the path app must match."""
    biz = business(services)
    plan = asyncio.run(services.plans.live.get("soneeva", "per-minute"))
    services.payment.live.subscribe("snapback", biz, plan, "sub_x")
    event = {"id": "evt_x", "type": "checkout.session.completed", "data": {"object": {
        "subscription": "sub_x", "metadata": {"app": "snapback", "tenant": biz}}}}
    response = client.post("/webhooks/stripe/soneeva", content=json.dumps(event).encode())
    assert response.json() == {"result": "unmapped"}
    assert services.voice._vault.versions == {}  # no voice env for another app's checkout


def test_product_events_of_this_app_drop_the_plan_cache(client, services):
    business(services)
    asyncio.run(services.plans.live.for_app("soneeva"))
    calls = services.payment.live.calls
    other = {"id": "evt_p1", "type": "product.updated", "data": {"object": {"metadata": {"app": "x"}}}}
    assert client.post("/webhooks/stripe/soneeva", content=json.dumps(other)).json()["result"] == "unmapped"
    mine = {"id": "evt_p2", "type": "price.created", "data": {"object": {"metadata": {"app": "soneeva"}}}}
    assert client.post("/webhooks/stripe/soneeva", content=json.dumps(mine)).json()["result"] == "processed"
    asyncio.run(services.plans.live.for_app("soneeva"))
    assert services.payment.live.calls == calls + 1


def test_provisioning_needs_a_public_https_base_url(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    refused = client.post(f"{APPS}/soneeva/webhook", headers=headers())
    assert refused.status_code == 400 and services.payment.live.endpoints == []


def test_provisioning_stores_the_secret_and_never_returns_it(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    client.app.state.settings = type("S", (), {"service_base_url": "https://hub.example"})()
    made = client.post(f"{APPS}/soneeva/webhook", headers=headers())
    assert made.status_code == 201
    assert made.json() == {"endpointId": "we_1", "url": "https://hub.example/webhooks/stripe/soneeva",
                           "hasWebhookSecret": True}
    url, events = services.payment.live.endpoints[0]
    assert set(events) == set(WEBHOOK_EVENTS)
    assert services.db.rows[("apps", "soneeva")]["stripeWebhookSecret"] == "whsec_1"
    shown = client.get(f"{APPS}/soneeva", headers=headers())
    assert shown.json()["hasWebhookSecret"] is True and "whsec_1" not in shown.text


def test_the_secret_is_write_only_and_a_blank_keeps_it(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    saved = client.patch(f"{APPS}/soneeva", json={"webhookSecret": "whsec_abc"}, headers=headers())
    assert saved.json()["hasWebhookSecret"] is True and "whsec_abc" not in saved.text
    client.patch(f"{APPS}/soneeva", json={"webhookSecret": "", "name": "S"}, headers=headers())
    assert services.db.rows[("apps", "soneeva")]["stripeWebhookSecret"] == "whsec_abc"
    assert services.keys._cache.get("soneeva") is None  # the verifier re-reads it at once


def test_the_staging_secret_is_its_own_write_only_field(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    client.patch(f"{APPS}/soneeva", json={"stagingWebhookSecret": "whsec_t"}, headers=headers())
    row = services.db.rows[("apps", "soneeva")]
    assert row["stagingWebhookSecret"] == "whsec_t" and not row.get("stripeWebhookSecret")
    assert client.get(f"{APPS}/soneeva_staging", headers=headers()).json()["hasWebhookSecret"] is True
    assert client.get(f"{APPS}/soneeva", headers=headers()).json()["hasWebhookSecret"] is False
    client.patch(f"{APPS}/soneeva", json={"stagingWebhookSecret": " "}, headers=headers())
    assert services.db.rows[("apps", "soneeva")]["stagingWebhookSecret"] == "whsec_t"


def test_a_tenant_is_read_once_until_its_subscription_event_arrives(client, services):
    from tests.setup.platform import subscribe

    biz = business(services)
    record = lambda: asyncio.run(services.billing.live.get_record("soneeva", biz))  # noqa: E731
    assert record().status == "none"
    subscribe(services, biz)
    calls = services.payment.live.calls
    assert record().status == "none" and services.payment.live.calls == calls  # cached
    event = {"id": "evt_s1", "type": "customer.subscription.created", "data": {"object": {"id": "sub_1"}}}
    assert client.post("/webhooks/stripe/soneeva", content=json.dumps(event)).json()["result"] == "processed"
    assert record().status == "active" and record().plan_code == "per-minute"
    assert client.post("/webhooks/stripe/soneeva", content=json.dumps(event)).json()["result"] == "duplicate"
