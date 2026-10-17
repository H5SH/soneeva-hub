"""Serviceperson handoff to the console, and the console's billing calls for a business."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from src.models.admin import Scope
from tests.setup.platform import business

CONSOLE = [Scope.HUB_BILLING_VIEW, Scope.HUB_BILLING_UPDATE]
SUCCESS = {"plan": "per-minute", "successUrl": "https://console.soneeva.com/billing?ok=1",
           "cancelUrl": "http://localhost:3000/billing"}


def _grant(services, biz: str, **fields) -> None:
    until = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    grant = {"scopes": ["calls.view"], "until": until} | fields
    services.business_dbs[f"b-{biz}"].rows[("grants", "sp")] = grant


def _serviceperson(services, scopes=("Businesses.Access",)) -> None:
    services.db.rows[("users", "sp")] = {"role": "serviceperson"}
    services.db.rows[("roles", "serviceperson")] = {"scopes": list(scopes)}


def _handoff(client, bearer, biz: str, sub: str = "-"):
    headers = bearer("karbon-admin", [Scope.HUB_HANDOFF_CREATE], sub=sub, uid="sp")
    return client.post("/v1/handoff", json={"businessId": biz}, headers=headers)


def _code(url: str) -> str:
    assert url.startswith("https://console.soneeva.com/handoff?code=")
    return parse_qs(urlparse(url).query)["code"][0]


def _redeem(client, bearer, code: str, app: str = "soneeva", sub: str = "-"):
    headers = bearer(app, [Scope.HUB_HANDOFF_REDEEM], sub=sub)
    return client.post("/v1/handoff/redeem", json={"code": code}, headers=headers)


def test_handoff_code_is_redeemed_once_by_the_console(client, bearer, services):
    biz = business(services)
    _serviceperson(services)
    services.accounts.add("sp", email="sanne@example.com")
    _grant(services, biz, roleId="helper")
    code = _code(_handoff(client, bearer, biz).json()["url"])
    row = services.db.rows[("handoffs", code)]
    assert row["used"] is False and row["expireAt"] - datetime.now(UTC) <= timedelta(seconds=60)
    redeemed = _redeem(client, bearer, code).json()
    assert (redeemed["uid"], redeemed["businessId"], redeemed["email"]) == ("sp", biz, "sanne@example.com")
    assert redeemed["grant"]["scopes"] == ["calls.view"] and redeemed["grant"]["roleId"] == "helper"
    assert _redeem(client, bearer, code).status_code == 410
    assert _redeem(client, bearer, "x" * 43).status_code == 404


def test_redeem_refuses_other_apps_other_businesses_expiry_and_a_lapsed_grant(client, bearer, services):
    biz = business(services)
    _serviceperson(services)
    _grant(services, biz)
    new = lambda: _code(_handoff(client, bearer, biz).json()["url"])  # noqa: E731
    assert _redeem(client, bearer, new(), app="karbon-admin").status_code == 403
    assert _redeem(client, bearer, new(), sub="other").status_code == 403
    assert _redeem(client, bearer, new(), sub=biz).status_code == 200
    late = new()
    services.db.rows[("handoffs", late)]["expireAt"] = datetime.now(UTC) - timedelta(seconds=1)
    assert _redeem(client, bearer, late).status_code == 410
    code = new()
    _grant(services, biz, blocked=True)
    assert _redeem(client, bearer, code).status_code == 403


def test_handoff_is_refused_without_access_or_a_running_grant(client, bearer, services):
    biz = business(services)
    _serviceperson(services)
    assert _handoff(client, bearer, biz).status_code == 403
    _grant(services, biz, blocked=True)
    assert _handoff(client, bearer, biz).status_code == 403
    _grant(services, biz, until=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    assert _handoff(client, bearer, biz).status_code == 403
    _grant(services, biz)
    assert _handoff(client, bearer, biz, sub=biz).status_code == 403
    assert _handoff(client, bearer, "nope").status_code == 404
    _serviceperson(services, scopes=["Businesses.View"])
    assert _handoff(client, bearer, biz).status_code == 403


def test_console_reads_plans_and_starts_checkout_and_portal(client, bearer, services):
    biz = business(services)
    owner = bearer("soneeva", CONSOLE, sub=biz, uid="owner1")
    listed = client.get(f"/v1/businesses/{biz}/plans", headers=owner).json()
    assert [p["code"] for p in listed["plans"]] == ["per-minute"]
    assert listed["subscription"]["status"] == "none"
    started = client.post(f"/v1/businesses/{biz}/checkout", json=SUCCESS,
                          headers=bearer("soneeva", CONSOLE, sub=biz, uid="owner1"))
    assert started.json() == {"url": "https://checkout.stripe.test/cs_1"}
    assert services.payment.live.checkouts[0]["metadata"] == {"app": "soneeva", "tenant": biz}
    portal = client.post(f"/v1/businesses/{biz}/portal", json={"returnUrl": SUCCESS["cancelUrl"]},
                         headers=bearer("soneeva", CONSOLE, sub=biz, uid="owner1"))
    assert portal.json() == {"url": f"https://billing.stripe.test/cus_{biz}"}


def test_billing_needs_the_business_subject_billing_scope_and_allowed_urls(client, bearer, services):
    biz = business(services)
    as_ = lambda sub=biz, uid="owner1": bearer("soneeva", CONSOLE, sub=sub, uid=uid)  # noqa: E731
    assert client.get(f"/v1/businesses/{biz}/plans", headers=as_(sub="-")).status_code == 403
    services.business_dbs[f"b-{biz}"].rows[("users", "staff1")] = {"role": "staff"}
    assert client.get(f"/v1/businesses/{biz}/plans", headers=as_(uid="staff1")).status_code == 403
    evil = SUCCESS | {"successUrl": "https://evil.example/"}
    assert client.post(f"/v1/businesses/{biz}/checkout", json=evil, headers=as_()).status_code == 422
