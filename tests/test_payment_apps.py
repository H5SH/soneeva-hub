"""/v1/payment/apps: apps, keys, plans and coupons, gated by the person's payment.* scopes."""

from src.models.admin import Scope
from tests.setup.platform import business
from tests.setup.staff import ADMIN_APP, staff

APPS = "/v1/payment/apps"


def test_me_shows_role_scopes_and_limits(client, bearer, services):
    services.db.rows[("roles", "superadmin")] = {"scopes": [str(s) for s in Scope] + ["*", "Users.View"]}
    me = client.get("/v1/me", headers=staff(services, bearer)()).json()
    assert me["uid"] == "op" and me["role"] == "superadmin" and me["allApps"] is True
    assert "payment.admin" in me["paymentScopes"] and "hub.payment.view" not in me["paymentScopes"]
    assert {"*", "Users.View", "platform.docs.view"} <= set(me["platformScopes"])


def test_the_app_scope_a_person_and_sub_dash_are_required(client, bearer, services):
    headers = staff(services, bearer)
    assert client.get(APPS, headers=bearer("bare", [], uid="op")).status_code == 403
    assert client.get(APPS, headers=bearer("karbon-admin", ADMIN_APP, uid="nobody")).status_code == 403
    business_token = bearer("karbon-admin", ADMIN_APP, sub="biz1", uid="op")
    assert client.get(APPS, headers=business_token).status_code == 403
    assert client.get(APPS, headers=headers()).status_code == 200
    services.db.rows[("users", "op")]["disabled"] = True
    assert client.get(APPS, headers=headers()).status_code == 403


def test_create_update_rotate_and_delete_an_app(client, bearer, services):
    headers = staff(services, bearer)
    created = client.post(APPS, json={"id": "snapback", "name": "Snapback",
                                      "allowedReturnHosts": ["x.example"]}, headers=headers())
    assert created.status_code == 201 and created.json()["allowedReturnHosts"] == ["x.example"]
    assert client.post(APPS, json={"id": "snapback"}, headers=headers()).status_code == 400
    patched = client.patch(f"{APPS}/snapback", json={"returnUrl": "https://x.example/b"}, headers=headers())
    assert patched.json()["returnUrl"] == "https://x.example/b" and patched.json()["name"] == "Snapback"
    key = client.post(f"{APPS}/snapback/keys", headers=headers()).json()
    assert key["privateKey"].startswith("-----BEGIN PRIVATE KEY-----")
    keys = client.get(f"{APPS}/snapback", headers=headers()).json()["keys"]
    assert [k["kid"] for k in keys] == [key["kid"]]
    assert "snapback" in [a["id"] for a in client.get(APPS, headers=headers()).json()["apps"]]
    assert client.delete(f"{APPS}/snapback", headers=headers()).json() == {"deleted": True}
    assert client.get(f"{APPS}/snapback", headers=headers()).status_code == 404


def test_a_member_sees_only_their_apps_and_needs_each_scope(client, bearer, services):
    business(services)
    headers = staff(services, bearer, "viewer", "viewer", [Scope.APP_VIEW, Scope.PLAN_VIEW], apps=["soneeva"])
    assert [a["id"] for a in client.get(APPS, headers=headers()).json()["apps"]] == ["soneeva"]
    assert client.get(f"{APPS}/soneeva-voice", headers=headers()).status_code == 403
    assert client.post(APPS, json={"id": "mine"}, headers=headers()).status_code == 403
    assert client.post(f"{APPS}/soneeva/keys", headers=headers()).status_code == 403
    plans = client.get(f"{APPS}/soneeva/plans", headers=headers()).json()["plans"]
    assert {"per-minute", "base"} <= {p["code"] for p in plans}
    status = client.put(f"{APPS}/soneeva/plans/base/status", json={"status": "hidden"}, headers=headers())
    assert status.status_code == 403


def test_plans_create_update_status_and_delete(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    url = f"{APPS}/soneeva/plans/team"
    body = {"name": "Team", "amount": 4900, "features": ["Two numbers"]}
    saved = client.put(url, json=body, headers=headers())
    assert saved.status_code == 200 and saved.json()["priceId"] and saved.json()["active"] is True
    kept = client.put(url, json={"name": "Team v1"}, headers=headers()).json()
    assert kept["priceId"] == saved.json()["priceId"] and kept["amount"] == 4900
    assert kept["productId"] == "prod_soneeva_team" and kept["features"] == []
    again = client.put(url, json={"name": "Team", "amount": 5900}, headers=headers()).json()
    assert again["priceId"] != kept["priceId"] and kept["priceId"] in services.payment.live.archived
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    icon = client.put(f"{url}/icon", content=png, headers=headers() | {"content-type": "image/png"})
    assert icon.json()["icon"] == "https://files.stripe.test/team"
    shown = client.get(f"{APPS}/soneeva/plans", headers=headers()).json()["plans"]
    assert next(p for p in shown if p["code"] == "team")["icon"] == "https://files.stripe.test/team"
    bad = client.put(f"{url}/icon", content=b"text", headers=headers() | {"content-type": "text/plain"})
    assert bad.status_code == 415
    assert client.delete(f"{url}/icon", headers=headers()).json()["icon"] is None
    soon = client.put(f"{url}/status", json={"status": "soon"}, headers=headers()).json()
    assert soon["status"] == "soon" and soon["active"] is False
    assert client.put(f"{url}/status", json={"status": "gone"}, headers=headers()).status_code == 422
    assert client.delete(url, headers=headers()).json() == {"deleted": True}
    assert client.delete(url, headers=headers()).status_code == 404


def test_updating_a_plan_needs_update_creating_needs_create(client, bearer, services):
    business(services)
    headers = staff(services, bearer, "maker", "maker", [Scope.PLAN_CREATE], apps=["soneeva"])
    assert client.put(f"{APPS}/soneeva/plans/new", json={"name": "N"}, headers=headers()).status_code == 200
    assert client.put(f"{APPS}/soneeva/plans/base", json={"name": "B"}, headers=headers()).status_code == 403


def test_coupons_create_list_and_delete(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    created = client.post(f"{APPS}/soneeva/coupons", json={"code": "LAUNCH99", "maxRedemptions": 5},
                          headers=headers())
    assert created.status_code == 201 and created.json()["createdBy"] == "op@example.com"
    listed = client.get(f"{APPS}/soneeva/coupons", headers=headers()).json()["coupons"]
    assert [c["code"] for c in listed] == ["LAUNCH99"]
    assert client.delete(f"{APPS}/soneeva/coupons/LAUNCH99", headers=headers()).json() == {"deleted": True}
    assert client.get(f"{APPS}/soneeva/coupons", headers=headers()).json()["coupons"] == []
