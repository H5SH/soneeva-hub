"""`{appId}_staging`: the same app and keys in Stripe test mode; test businesses stay in test mode."""

import asyncio
import json

import pytest

from src.models.admin import Scope
from src.models.mode import Mode
from src.payment import FakePayment
from src.services.errors import PlatformError
from src.services.modes import ByMode
from tests.setup.platform import SIGNUP, business
from tests.setup.staff import staff

APPS = "/v1/payment/apps"
CONSOLE = [Scope.HUB_BUSINESS_CREATE, Scope.HUB_BUSINESS_VIEW, Scope.HUB_BILLING_VIEW]


def test_a_staging_caller_signs_up_a_test_business_and_only_reaches_test_businesses(client, bearer, services):
    live = business(services)
    signup = SIGNUP.model_dump() | {"ownerUid": None, "owner": {
        "email": "t@example.com", "password": "Secret-pass-1", "displayName": "T", "username": "tester"}}
    created = client.post("/v1/businesses", json=signup, headers=bearer("soneeva_staging", CONSOLE))
    assert created.status_code == 201, created.text
    test_id = created.json()["id"]
    assert created.json()["test"] is True and services.db.rows[("businesses", live)]["test"] is False
    listed = client.get("/v1/businesses", headers=bearer("soneeva_staging", CONSOLE)).json()["businesses"]
    assert [b["id"] for b in listed] == [test_id]
    assert client.get(f"/v1/businesses/{live}", headers=bearer("soneeva_staging", CONSOLE)).status_code == 403
    as_live_sub = bearer("soneeva_staging", CONSOLE, sub=live)
    assert client.get(f"/v1/businesses/{live}", headers=as_live_sub).status_code == 403
    both = client.get("/v1/businesses", headers=bearer("soneeva", CONSOLE)).json()["businesses"]
    assert {b["id"] for b in both} == {live, test_id}


def test_a_test_business_bills_in_test_mode_whoever_calls(client, bearer, services):
    biz = business(services)
    services.db.rows[("businesses", biz)]["test"] = True
    test_payment = services.payment[Mode.TEST]
    asyncio.run(services.plans[Mode.TEST].define("soneeva", "per-minute", "Per minute", 10, unit="minute"))
    plan = asyncio.run(services.plans[Mode.TEST].get("soneeva", "per-minute"))
    test_payment.subscribe("soneeva", biz, plan)
    voice = lambda: bearer("soneeva-voice", [Scope.HUB_USAGE_WRITE, Scope.HUB_PLAN_VIEW], sub=biz)  # noqa: E731
    usage = client.post("/v1/usage", json={"callId": "c1", "seconds": 30}, headers=voice())
    assert usage.json() == {"status": "recorded", "billed": True}
    assert "c1" in test_payment.usage and not services.payment.live.usage
    status = client.get(f"/v1/businesses/{biz}/plan", headers=voice())
    assert status.json() | {"periodEnd": None} == {"active": True, "status": "active", "plan": "per-minute",
                                                  "periodEnd": None, "test": True}


def test_test_plans_are_independent_and_the_staging_id_is_no_app_of_its_own(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    trial = {"name": "Trial", "amount": 0}
    put = client.put(f"{APPS}/soneeva_staging/plans/trial", json=trial, headers=headers())
    assert put.status_code == 200, put.text
    test_app = client.get(f"{APPS}/soneeva_staging", headers=headers()).json()
    live_app = client.get(f"{APPS}/soneeva", headers=headers()).json()
    assert test_app["mode"] == "test" and "trial" in [p["code"] for p in test_app["plans"]]
    assert live_app["mode"] == "live" and "trial" not in [p["code"] for p in live_app["plans"]]
    assert test_app["keys"] == live_app["keys"]
    for method, path in (("PATCH", ""), ("DELETE", ""), ("POST", "/keys")):
        response = client.request(method, f"{APPS}/soneeva_staging{path}", json={}, headers=headers())
        assert response.status_code == 404, (method, path)
    test_plans = client.get("/v1/public/plans?app=soneeva_staging").json()["plans"]
    assert "trial" in [p["code"] for p in test_plans]


def test_the_staging_webhook_uses_the_test_account_and_its_own_secret(client, bearer, services):
    biz = business(services)
    secrets = {"stripeWebhookSecret": "whsec_live", "stagingWebhookSecret": "whsec_t"}
    services.db.rows[("apps", "soneeva")] |= secrets
    event = {"id": "evt_t", "type": "customer.subscription.updated", "data": {"object": {"id": "sub_t"}}}
    plan = asyncio.run(services.plans.live.get("soneeva", "per-minute"))
    services.payment[Mode.TEST].subscribe("soneeva", biz, plan, "sub_t")
    response = client.post("/webhooks/stripe/soneeva_staging", content=json.dumps(event).encode())
    assert response.json() == {"result": "processed"}
    assert services.payment[Mode.TEST].webhook_secrets == ["whsec_t"]
    assert services.payment.live.webhook_secrets == []
    client.app.state.settings = type("S", (), {"service_base_url": "https://hub.soneeva.com"})()
    made = client.post(f"{APPS}/soneeva_staging/webhook", headers=staff(services, bearer)()).json()
    assert made["url"] == "https://hub.soneeva.com/webhooks/stripe/soneeva_staging"
    assert services.payment[Mode.TEST].endpoints and not services.payment.live.endpoints


def test_tools_have_no_staging_twin(client, bearer):
    headers = bearer("karbon-admin_staging", [Scope.HUB_BUSINESS_VIEW])
    assert client.get("/v1/businesses", headers=headers).status_code == 401


def test_an_app_with_subscribers_is_never_deleted(client, bearer, services):
    business(services, subscribed=True)
    headers = staff(services, bearer)
    assert client.get(f"{APPS}/soneeva", headers=headers()).json()["subscribers"] == 1
    refused = client.delete(f"{APPS}/soneeva", headers=headers())
    assert refused.status_code == 400 and "never deleted" in refused.json()["detail"]


def test_test_mode_off_is_a_clear_503():
    with pytest.raises(PlatformError, match="STRIPE_TEST_SECRET_KEY") as raised:
        ByMode(FakePayment())[Mode.TEST]
    assert raised.value.status == 503
