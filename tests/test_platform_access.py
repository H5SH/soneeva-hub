"""Platform people are checked on their platform scopes; a role never needs a hub.* id."""

from src.models.admin import Scope
from src.models.db import Default
from tests.setup.platform import business

APP = [Scope.HUB_BUSINESS_VIEW, Scope.HUB_BUSINESS_UPDATE, Scope.HUB_PLATFORM_VIEW]


def person(services, uid: str, scopes: list[str]) -> None:
    services.db.rows[(Default.ROLES.value, f"r-{uid}")] = {"scopes": scopes}
    services.db.rows[(Default.USERS.value, uid)] = {"uid": uid, "role": f"r-{uid}"}


def test_console_ids_open_the_business_endpoints(client, bearer, services):
    biz = business(services)
    person(services, "viewer", ["Businesses.View"])
    person(services, "nobody", ["hub.business.view", "hub.business.update"])
    person(services, "boss", ["*"])
    as_ = lambda uid: bearer("admin-app", APP, uid=uid)  # noqa: E731
    listed = client.get("/v1/businesses", headers=as_("viewer")).json()["businesses"]
    assert [b["id"] for b in listed] == [biz]
    assert client.get("/v1/businesses", headers=as_("nobody")).status_code == 403
    url = f"/v1/businesses/{biz}"
    assert client.patch(url, json={"configured": True}, headers=as_("viewer")).status_code == 403
    assert client.patch(url, json={"configured": True}, headers=as_("boss")).json()["configured"] is True


def test_assigned_me_lists_only_the_callers_businesses(client, bearer, services):
    biz = business(services)
    services.db.rows[(Default.BUSINESSES.value, biz)]["servicepersonUid"] = "sp"
    person(services, "sp", ["Businesses.Access"])
    person(services, "other", ["Businesses.Access"])
    person(services, "viewer", ["Businesses.View"])
    mine = lambda uid: client.get("/v1/businesses?assigned=me", headers=bearer("admin-app", APP, uid=uid))  # noqa: E731
    assert [b["id"] for b in mine("sp").json()["businesses"]] == [biz]
    assert mine("other").json()["businesses"] == []
    assert mine("viewer").status_code == 403


def test_branches_for_the_platform_and_for_branches_view(client, bearer, services):
    biz = business(services)
    person(services, "viewer", ["Businesses.View"])
    person(services, "seller", ["Businesses.Assign"])
    url = f"/v1/businesses/{biz}/branches"
    listed = client.get(url, headers=bearer("admin-app", APP, uid="viewer")).json()["branches"]
    assert listed and set(listed[0]) == {"id", "name", "phone", "address"}
    assert client.get(url, headers=bearer("admin-app", APP, uid="seller")).status_code == 403
    bdb = services.business_dbs[f"b-{biz}"]
    bdb.rows[("roles", "staff")] = {"scopes": ["branches.view"]}
    bdb.rows[("roles", "cook")] = {"scopes": ["orders.view"]}
    bdb.rows[("users", "clerk")] = {"role": "staff"}
    bdb.rows[("users", "chef")] = {"role": "cook"}
    assert client.get(url, headers=bearer("admin-app", APP, sub=biz, uid="clerk")).status_code == 200
    assert client.get(url, headers=bearer("admin-app", APP, sub=biz, uid="chef")).status_code == 403


def test_businesses_access_opens_only_the_businesses_assigned_to_the_person(client, bearer, services):
    biz = business(services)
    person(services, "sp", ["Businesses.Access"])
    sp = lambda: bearer("admin-app", APP, uid="sp")  # noqa: E731
    reads = [f"/v1/businesses/{biz}{path}" for path in [
        "", "/branches", "/checklist", "/callers", "/ivr/numbers", "/audios"]]
    assert all(client.get(url, headers=sp()).status_code == 404 for url in reads)  # not theirs
    services.db.rows[(Default.BUSINESSES.value, biz)]["servicepersonUid"] = "sp"
    assert all(client.get(url, headers=sp()).status_code == 200 for url in reads)
    patch = client.patch(f"/v1/businesses/{biz}", json={"configured": True}, headers=sp())
    block = client.post(f"/v1/businesses/{biz}/callers", json={"phone": "+31612345678"}, headers=sp())
    assert patch.status_code == 403 and block.json()["detail"] == "Requires Businesses.Write"


def test_a_role_of_person_scopes_only_reaches_all_they_allow(client, bearer, services):
    biz = business(services)
    person(services, "ops", ["Businesses.View", "Businesses.Write", "Users.View", str(Scope.APP_VIEW)])
    app = [*APP, Scope.HUB_PEOPLE_VIEW, Scope.HUB_PAYMENT_VIEW]
    ops = lambda: bearer("admin-app", app, uid="ops")  # noqa: E731
    assert client.get("/v1/businesses", headers=ops()).status_code == 200
    assert client.patch(f"/v1/businesses/{biz}", json={"configured": True}, headers=ops()).status_code == 200
    assert client.get("/v1/people", headers=ops()).status_code == 200
    assert client.get("/v1/payment/apps", headers=ops()).status_code == 200
    me = client.get("/v1/me", headers=ops()).json()
    assert me["paymentScopes"] == [str(Scope.APP_VIEW)] and "Users.View" in me["platformScopes"]
