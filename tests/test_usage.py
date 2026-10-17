"""POST /v1/usage: straight to the subscription's Stripe meter, once per call id."""

from src.models.admin import Scope
from tests.setup.platform import business, subscribe


def report(client, bearer, business_id, call="call-1", seconds=95):
    headers = bearer("soneeva-voice", [Scope.HUB_USAGE_WRITE], sub=business_id)
    return client.post("/v1/usage", json={"callId": call, "seconds": seconds}, headers=headers)


def test_usage_is_metered_once_per_call(client, bearer, services):
    biz = business(services, subscribed=True)
    assert report(client, bearer, biz).json() == {"status": "recorded", "billed": True}
    assert report(client, bearer, biz).json() == {"status": "duplicate", "billed": True}
    assert services.payment.live.usage == {"call-1": ("mtr_soneeva_per-minute", f"cus_{biz}", 95)}


def test_usage_without_a_plan_is_not_billed(client, bearer, services):
    biz = business(services)
    assert report(client, bearer, biz).json() == {"status": "recorded", "billed": False}
    assert services.payment.live.usage == {}


def test_a_new_subscription_is_seen_once_the_webhook_drops_the_cache(client, bearer, services):
    biz = business(services)
    report(client, bearer, biz)
    subscribe(services, biz)
    services.billing.live.forget("soneeva", biz)
    assert report(client, bearer, biz, call="call-2").json()["billed"] is True


def test_usage_needs_a_business_subject(client, bearer, services):
    business(services)
    headers = bearer("soneeva-voice", [Scope.HUB_USAGE_WRITE])
    assert client.post("/v1/usage", json={"callId": "c", "seconds": 1}, headers=headers).status_code == 403
