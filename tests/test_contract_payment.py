"""The /v1 contract of the internal app (karbon-admin) and the console's billing and handoff."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from src.models.admin import Scope
from tests.setup.platform import business
from tests.setup.staff import staff
from tests.test_contract import FIXTURE

APPS = "/v1/payment/apps"
DUMP: dict = {}


def same_keys(actual: dict, example: dict) -> None:
    """Keys match all the way down; a null object or empty list in the response is not compared."""
    assert set(actual) == set(example)
    for key, value in example.items():
        if isinstance(value, dict) and actual[key] is not None and key not in ("roles", "holders"):
            same_keys(actual[key], value)
        if isinstance(value, list) and value and isinstance(value[0], dict) and actual[key]:
            same_keys(actual[key][0], value[0])


def check(name: str, actual: dict, top_only: bool = False) -> None:
    if os.getenv("CONTRACT_DUMP"):
        DUMP[name] = actual
        return
    if top_only:
        assert set(actual) == set(FIXTURE[name]), name
    else:
        same_keys(actual, FIXTURE[name])


@pytest.fixture
def admin(client, bearer, services):
    biz = business(services, subscribed=True)
    client.post("/v1/usage", json={"callId": "CA1", "seconds": 61},
                headers=bearer("soneeva-voice", [Scope.HUB_USAGE_WRITE], sub=biz))
    services.db.rows[("roles", "superadmin")] = {"scopes": [str(s) for s in Scope] + ["*"]}
    return biz, staff(services, bearer)


def test_me_apps_plans_and_coupons(admin, client):
    _, headers = admin
    check("me", client.get("/v1/me", headers=headers()).json())
    check("paymentApp", client.post(APPS, json=FIXTURE["appCreate"], headers=headers()).json())
    check("paymentApps", client.get(APPS, headers=headers()).json())
    check("appKey", client.post(f"{APPS}/soneeva/keys", headers=headers()).json())
    plan = client.put(f"{APPS}/soneeva/plans/team", json=FIXTURE["planPut"], headers=headers())
    check("paymentPlan", plan.json())
    created = client.post(f"{APPS}/soneeva/plans", json=FIXTURE["planCreate"], headers=headers())
    check("paymentPlan", created.json())
    check("paymentApp", client.patch(f"{APPS}/honeybook", json=FIXTURE["appPatch"], headers=headers()).json())
    client.app.state.settings = type("S", (), {"service_base_url": "https://hub.soneeva.com"})()
    check("webhookProvisioned", client.post(f"{APPS}/soneeva/webhook", headers=headers()).json())
    minted = client.post("/v1/agent-tokens", json=FIXTURE["agentTokenCreate"], headers=headers())
    check("agentToken", minted.json())
    check("agentTokensRevoked", client.request("DELETE", "/v1/agent-tokens", headers=headers()).json())
    check("paymentPlans", client.get(f"{APPS}/soneeva/plans", headers=headers()).json())
    coupon = client.post(f"{APPS}/soneeva/coupons", json=FIXTURE["couponCreate"], headers=headers())
    check("coupon", coupon.json())
    check("coupons", client.get(f"{APPS}/soneeva/coupons", headers=headers()).json())


def test_payment_businesses(admin, client):
    biz, headers = admin
    url = f"/v1/payment/businesses/{biz}"
    check("paymentBusinesses", client.get("/v1/payment/businesses", headers=headers()).json())
    check("paymentBusiness", client.get(url, headers=headers()).json())
    check("invoices", client.get(f"{url}/invoices", headers=headers()).json())
    check("usage", client.get(f"{url}/usage", headers=headers()).json())


def test_handoff_and_console_billing(admin, client, bearer, services):
    biz, _ = admin
    services.db.rows[("users", "sp")] = {"role": "serviceperson"}
    services.db.rows[("roles", "serviceperson")] = {"scopes": ["Businesses.Access"]}
    until = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    services.business_dbs[f"b-{biz}"].rows[("grants", "sp")] = {"scopes": [], "until": until}
    headers = bearer("karbon-admin", [Scope.HUB_HANDOFF_CREATE], uid="sp")
    handoff = client.post("/v1/handoff", json={"businessId": biz}, headers=headers).json()
    check("handoff", handoff)
    services.accounts.add("sp", email="sp@example.com")
    code = handoff["url"].split("code=")[1]
    console = [Scope.HUB_BILLING_VIEW, Scope.HUB_BILLING_UPDATE, Scope.HUB_HANDOFF_REDEEM]
    redeem = bearer("soneeva", console)
    check("handoffRedeemed", client.post("/v1/handoff/redeem", json={"code": code}, headers=redeem).json())
    owner = lambda: bearer("soneeva", console, sub=biz, uid="owner1")  # noqa: E731
    check("billingPlans", client.get(f"/v1/businesses/{biz}/plans", headers=owner()).json())
    asyncio.run(services.payment.live.cancel_subscription("sub_1", at_period_end=False))
    services.billing.live.forget("soneeva", biz)
    started = client.post(f"/v1/businesses/{biz}/checkout", json=FIXTURE["checkoutRequest"], headers=owner())
    check("checkout", started.json())
    portal = client.post(f"/v1/businesses/{biz}/portal", json=FIXTURE["portalRequest"], headers=owner())
    check("portal", portal.json())


def teardown_module() -> None:
    if os.getenv("CONTRACT_DUMP"):
        import json
        from pathlib import Path

        Path(os.environ["CONTRACT_DUMP"]).write_text(json.dumps(DUMP, indent=2, default=str))
