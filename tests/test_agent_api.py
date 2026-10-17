"""Operator tokens and /v1/agent: identity only, re-checked on every call."""

from datetime import UTC, datetime, timedelta

from src.models.admin import Scope
from src.services.agent_tokens import digest
from tests.setup.platform import business
from tests.setup.staff import staff

AGENT = "/v1/agent"
SUPER = [str(s) for s in Scope] + ["*"]


def mint(client, services, bearer, uid="op", role="superadmin", scopes=None, minutes=30, apps=None):
    services.db.rows[("roles", role)] = {"scopes": scopes if scopes is not None else SUPER}
    headers = staff(services, bearer, uid, role, apps=apps)
    return client.post("/v1/agent-tokens", json={"minutes": minutes}, headers=headers()), headers


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_a_token_is_shown_once_and_only_its_hash_is_stored(client, bearer, services):
    made, _ = mint(client, services, bearer)
    token = made.json()["token"]
    assert made.status_code == 201 and token.startswith("kat_") and made.json()["expiresAt"]
    rows = {k: v for k, v in services.db.rows.items() if k[0] == "agentTokens"}
    assert list(rows) == [("agentTokens", digest(token))] and token not in str(rows)
    me = client.get(f"{AGENT}/whoami", headers=auth(token)).json()
    assert me["uid"] == "op" and "payment.app.create" in me["paymentScopes"] and me["expiresAt"]


def test_minutes_are_bounded_and_agent_use_is_required(client, bearer, services):
    assert mint(client, services, bearer, minutes=5)[0].status_code == 422
    assert mint(client, services, bearer, minutes=121)[0].status_code == 422
    refused, _ = mint(client, services, bearer, "seller", "sales", [str(Scope.APP_VIEW)])
    assert refused.status_code == 403


def test_expiry_revocation_and_a_disabled_person_all_401(client, bearer, services):
    made, headers = mint(client, services, bearer)
    token = made.json()["token"]
    services.db.rows[("agentTokens", digest(token))]["expiresAt"] = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get(f"{AGENT}/whoami", headers=auth(token)).status_code == 401

    token = mint(client, services, bearer)[0].json()["token"]
    services.db.rows[("users", "op")]["disabled"] = True
    assert client.get(f"{AGENT}/whoami", headers=auth(token)).status_code == 401
    services.db.rows[("users", "op")]["disabled"] = False
    assert client.get(f"{AGENT}/whoami", headers=auth(token)).status_code == 200
    assert client.request("DELETE", "/v1/agent-tokens", headers=headers()).json() == {"revoked": 2}
    assert client.get(f"{AGENT}/whoami", headers=auth(token)).status_code == 401


def test_scopes_narrow_with_the_role_on_every_call(client, bearer, services):
    business(services)
    scopes = ["Agent.Use", str(Scope.APP_VIEW)]
    token = mint(client, services, bearer, "viewer", "viewer", scopes, apps=["soneeva"])[0].json()["token"]
    assert [a["id"] for a in client.get(f"{AGENT}/apps", headers=auth(token)).json()["apps"]] == ["soneeva"]
    assert client.get(f"{AGENT}/apps/soneeva-voice", headers=auth(token)).status_code == 403
    assert client.post(f"{AGENT}/apps", json={"id": "mine"}, headers=auth(token)).status_code == 403
    assert client.post(f"{AGENT}/apps/soneeva/keys", headers=auth(token)).status_code == 403
    services.db.rows[("roles", "viewer")]["scopes"] = [str(Scope.APP_VIEW)]  # Agent.Use taken away
    services.roles.forget("viewer")
    assert client.get(f"{AGENT}/apps", headers=auth(token)).status_code == 401


def test_the_setup_an_agent_does(client, bearer, services):
    token = mint(client, services, bearer)[0].json()["token"]
    h = auth(token)
    app = client.post(f"{AGENT}/apps", json={"id": "snapback", "name": "Snapback", "tags": ["Acme"],
                                             "allowedReturnHosts": ["snap.example"]}, headers=h)
    assert app.status_code == 201 and app.json()["tags"] == ["acme"]
    body = {"name": "Pro Plus", "amount": 2900}
    plan = client.post(f"{AGENT}/apps/snapback/plans", json=body, headers=h).json()
    assert plan["code"] == "pro-plus" and plan["priceId"]
    free = client.post(f"{AGENT}/apps/snapback/plans", json={"name": "Free"}, headers=h).json()
    patched = client.patch(f"{AGENT}/apps/snapback", json={"freePlanCode": free["code"]}, headers=h).json()
    assert patched["freePlanCode"] == "free"
    env = client.post(f"{AGENT}/apps/snapback/keys?format=env", headers=h)
    assert env.headers["content-type"].startswith("text/plain")
    assert env.text.startswith("PAYMENT_APPLICATION_ID=snapback\nPAYMENT_KEY_ID=")
    assert "PAYMENT_PRIVATE_KEY=" in env.text
    coupon = client.post(f"{AGENT}/apps/snapback/coupons", json={"code": "LAUNCH99", "value": 50}, headers=h)
    assert coupon.status_code == 201 and coupon.json()["discount"]["value"] == 50
    client.app.state.settings = type("S", (), {"service_base_url": "https://hub.example"})()
    hook = client.post(f"{AGENT}/apps/snapback/webhook", headers=h).json()
    assert hook["hasWebhookSecret"] is True and "secret" not in hook
    assert client.get(f"{AGENT}/apps/snapback", headers=h).json()["hasWebhookSecret"] is True
    seen = client.get(f"{AGENT}/apps/snapback/tenants/t1", headers=h).json()
    assert seen["active"] is False
