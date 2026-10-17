"""The onboarding items every checklist starts with, and who moves them."""

import asyncio

from src.models.admin import Scope
from src.models.db import Default
from tests.setup.platform import business, checkout_event, subscribe

ADMIN = [Scope.HUB_BUSINESS_VIEW, Scope.HUB_BUSINESS_UPDATE]


def _items(services, biz: str) -> dict[str, dict]:
    bdb = services.business_dbs[f"b-{biz}"]
    return {k[1]: v for k, v in bdb.rows.items() if k[0] == "checklist"}


def _person(services, uid: str, role: str) -> None:
    services.db.rows[(Default.USERS.value, uid)] = {"uid": uid, "role": role}


def test_every_checklist_starts_with_the_onboarding_items(services):
    items = _items(services, business(services))
    ordered = sorted(items, key=lambda i: items[i]["order"])
    assert ordered[:5] == ["assigned", "meeting", "subscribed", "mcp", "profile"]
    assert items["meeting"]["label"] == "Meeting with the serviceperson"
    assert all(i["state"] == "open" for i in items.values())


def test_assigning_a_serviceperson_ticks_assigned_and_unassigning_reopens(client, bearer, services):
    biz = business(services)
    services.db.rows[(Default.USERS.value, "sp1")] = {"role": "serviceperson"}
    url = f"/v1/businesses/{biz}"
    assigned = client.patch(url, json={"servicepersonUid": "sp1"}, headers=bearer("soneeva", ADMIN))
    assert assigned.status_code == 200
    assert _items(services, biz)["assigned"]["state"] == "done"
    client.patch(url, json={"servicepersonUid": None}, headers=bearer("soneeva", ADMIN))
    assert _items(services, biz)["assigned"]["state"] == "open"


def test_a_checkout_ticks_subscribed_and_a_lapsed_subscription_reopens(client, services):
    biz = business(services)
    subscribe(services, biz)
    client.post("/webhooks/stripe/soneeva", content=checkout_event(biz))
    assert _items(services, biz)["subscribed"]["state"] == "done"
    asyncio.run(services.businesses.checklist.on_subscription(biz, False))
    assert _items(services, biz)["subscribed"]["state"] == "open"
    asyncio.run(services.businesses.checklist.on_subscription("not-a-business", True))  # skipped


def test_only_the_assigned_serviceperson_or_write_ticks_the_meeting(client, bearer, services):
    biz = business(services)
    url = f"/v1/businesses/{biz}/checklist/meeting"
    _person(services, "sp", "serviceperson")
    as_sp = lambda: bearer("karbon-admin", ADMIN, uid="sp")  # noqa: E731 - tokens are single use
    assert client.post(url, json={"done": True}, headers=as_sp()).status_code == 404  # not theirs
    services.db.rows[("businesses", biz)]["servicepersonUid"] = "sp"
    done = client.post(url, json={"done": True}, headers=as_sp())
    assert done.json() == {"id": "meeting", "state": "done"}
    assert _items(services, biz)["meeting"]["by"] == "sp"
    as_owner_token = bearer("soneeva", ADMIN, sub=biz)
    assert client.post(url, json={"done": True}, headers=as_owner_token).status_code == 403
    _person(services, "boss", "superadmin")
    as_boss = bearer("karbon-admin", ADMIN, uid="boss")
    assert client.post(url, json={"done": False}, headers=as_boss).status_code == 200
    assert _items(services, biz)["meeting"]["state"] == "open"
    _person(services, "sales1", "sales")
    as_sales = bearer("karbon-admin", ADMIN, uid="sales1")
    assert client.post(url, json={"done": True}, headers=as_sales).status_code == 403


def test_a_serviceperson_cannot_patch_through_the_meeting_scope(client, bearer, services):
    biz = business(services)
    _person(services, "sp", "serviceperson")
    services.db.rows[("businesses", biz)]["servicepersonUid"] = "sp"
    as_sp = bearer("karbon-admin", ADMIN, uid="sp")
    patch = client.patch(f"/v1/businesses/{biz}", json={"status": "running"}, headers=as_sp)
    assert patch.status_code == 403


def test_reading_the_checklist(client, bearer, services):
    from tests.test_contract import FIXTURE, same_keys

    biz = business(services)
    url = f"/v1/businesses/{biz}/checklist"
    view = [Scope.HUB_BUSINESS_VIEW]
    _person(services, "boss", "superadmin")
    items = client.get(url, headers=bearer("karbon-admin", view, uid="boss")).json()["items"]
    assert [i["id"] for i in items][:4] == ["assigned", "meeting", "subscribed", "mcp"]
    same_keys(items[0], FIXTURE["checklist"]["items"][0])
    services.db.rows[("roles", "helper")] = {"scopes": ["Businesses.Access"]}
    _person(services, "sp", "helper")
    sp = lambda: bearer("karbon-admin", view, uid="sp")  # noqa: E731
    assert client.get(url, headers=sp()).status_code == 404  # Access alone reaches only assigned ones
    services.db.rows[("businesses", biz)]["servicepersonUid"] = "sp"
    assert client.get(url, headers=sp()).status_code == 200
    owner = bearer("soneeva", view, sub=biz, uid="owner1")
    assert client.get(url, headers=owner).status_code == 200
    staff = services.business_dbs[f"b-{biz}"]
    staff.rows[("users", "st1")] = {"role": "staff"}
    assert client.get(url, headers=bearer("soneeva", view, sub=biz, uid="st1")).status_code == 403
