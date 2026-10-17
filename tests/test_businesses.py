"""Sign-up provisioning and the scoped business API."""


import pytest

from src.models.admin import Scope
from src.models.db import Default

CONSOLE = [Scope.HUB_BUSINESS_CREATE, Scope.HUB_BUSINESS_VIEW, Scope.HUB_BUSINESS_UPDATE,
           Scope.HUB_SERVICEPERSON_VIEW]
SIGNUP = {"ownerUid": "owner1", "name": "Pizza Roma", "type": "restaurant", "country": "nl",
          "timezone": "Europe/Amsterdam", "languages": ["nl", "en"], "branchCount": 2,
          "phone": "+31201234567", "email": "roma@example.com",
          "hours": {"mon": [{"open": "09:00", "close": "17:00"}], "sat": [{"open": "10:00", "close": "14:00"},
                                                                     {"open": "17:00", "close": "01:00"}]}}


@pytest.fixture
def created(client, bearer):
    response = client.post("/v1/businesses", json=SIGNUP, headers=bearer("soneeva", CONSOLE))
    assert response.status_code == 201, response.text
    return response.json()


def test_signup_provisions_the_business_database(created, services):
    db_id = created["dbId"]
    assert db_id == f"b-{created['id']}" and db_id in services.db.databases
    assert db_id in services.db.indexed
    bdb = services.business_dbs[db_id]
    assert bdb.rows[("roles", "owner")]["scopes"][0] == "overview.view"  # '*' expanded from catalogue
    assert {k for (c, k) in bdb.rows if c == "roles"} == {"owner"}  # every other role is the business's own
    assert ("checklist", "menu") in bdb.rows  # food type
    assert bdb.rows[("users", "owner1")]["role"] == "owner"
    assert {"name", "email", "phone"} <= set(bdb.rows[("users", "owner1")]["profile"])  # as the console reads
    assert bdb.rows[("branches", "main")]["phone"] == "+31201234567"
    hours = bdb.rows[("branches", "main")]["hours"]
    assert hours["mon"] == [{"open": "09:00", "close": "17:00"}] and hours["sun"] == []
    assert len(hours["sat"]) == 2
    assert bdb.rows[("checklist", "menu")]["label"] == "Upload the menu"
    assert bdb.rows[("settings", "agent")]["language"] == "auto"
    assert services.accounts.claims["owner1"] == {"businessId": created["id"], "username": "owner1"}
    assert created["status"] == "pending" and not created["provisioned"]  # answered before provisioning
    assert services.db.rows[("businesses", created["id"])]["provisioned"]


@pytest.mark.parametrize("hours", [
    "Mon-Sun 11:00-22:00",
    {"mon": [{"open": "9:00", "close": "17:00"}]},
    {"mon": [{"open": "09:00", "close": "09:00"}]},
    {"mon": [{"open": "09:00", "close": "10:00"}] * 3},
    {"holiday": []},
])
def test_signup_refuses_hours_that_are_not_a_week(client, bearer, hours):
    body = SIGNUP | {"hours": hours}
    assert client.post("/v1/businesses", json=body, headers=bearer("soneeva", CONSOLE)).status_code == 422


def test_a_google_signup_chooses_its_username(client, bearer, services):
    body = SIGNUP | {"ownerUsername": "pizza.roma"}
    response = client.post("/v1/businesses", json=body, headers=bearer("soneeva", CONSOLE))
    assert response.status_code == 201, response.text
    assert services.accounts.claims["owner1"]["username"] == "pizza.roma"


def test_signup_is_idempotent_per_owner(created, client, bearer):
    again = client.post("/v1/businesses", json=SIGNUP, headers=bearer("soneeva", CONSOLE))
    assert again.json()["id"] == created["id"]


def test_signup_rejects_an_unknown_type_and_a_missing_owner(client, bearer):
    headers = bearer("soneeva", CONSOLE)
    assert client.post("/v1/businesses", json=SIGNUP | {"type": "casino"}, headers=headers).status_code == 422
    missing = SIGNUP | {"ownerUid": "missing-1"}
    assert client.post("/v1/businesses", json=missing, headers=bearer("soneeva", CONSOLE)).status_code == 404


def test_signup_refuses_a_platform_account_as_owner(client, bearer, services):
    services.db.rows[(Default.USERS.value, "owner1")] = {"uid": "owner1", "role": "sales"}
    response = client.post("/v1/businesses", json=SIGNUP, headers=bearer("soneeva", CONSOLE))
    assert response.status_code == 403 and "businessId" not in services.accounts.claims["owner1"]


def test_a_business_subject_sees_only_itself(created, client, bearer):
    url = f"/v1/businesses/{created['id']}"
    mine = client.get(url, headers=bearer("soneeva", CONSOLE, sub=created["id"]))
    other = client.get(url, headers=bearer("soneeva", CONSOLE, sub="zzz"))
    assert mine.status_code == 200 and other.status_code == 404


def test_a_person_is_limited_by_role_and_businesses(created, client, bearer, services):
    services.db.rows[(Default.USERS.value, "sp1")] = {"uid": "sp1", "role": "sales", "businesses": ["x"]}
    listed = client.get("/v1/businesses", headers=bearer("soneeva", CONSOLE, uid="sp1")).json()
    assert listed["businesses"] == []
    patch = client.patch(f"/v1/businesses/{created['id']}", json={"status": "running"},
                         headers=bearer("soneeva", CONSOLE, uid="sp1"))
    assert patch.status_code == 404  # outside their businesses[]
    services.db.rows[(Default.USERS.value, "sp1")]["businesses"] = []
    patch = client.patch(f"/v1/businesses/{created['id']}", json={"status": "running"},
                         headers=bearer("soneeva", CONSOLE, uid="sp1"))
    assert patch.status_code == 403  # sales assigns (Businesses.Assign) but has no Businesses.Write


def test_patch_validates_the_serviceperson(created, client, bearer, services):
    url = f"/v1/businesses/{created['id']}"
    assign = lambda uid: client.patch(url, json={"servicepersonUid": uid}, headers=bearer("soneeva", CONSOLE))  # noqa: E731
    assert assign("nobody").status_code == 422
    services.db.rows[(Default.USERS.value, "sp-demo-1")] = {"role": "sales"}
    assert assign("sp-demo-1").status_code == 422
    services.db.rows[(Default.USERS.value, "sp-demo-1")] = {"role": "serviceperson", "disabled": True}
    assert assign("sp-demo-1").status_code == 422
    services.db.rows[(Default.USERS.value, "sp-demo-1")] = {"role": "serviceperson"}
    ok = client.patch(url, json={"servicepersonUid": "sp-demo-1", "status": "running", "configured": True},
                      headers=bearer("soneeva", CONSOLE))
    assert ok.json()["status"] == "running" and ok.json()["servicepersonUid"] == "sp-demo-1"


def test_servicepersons_are_the_people_with_that_role(client, bearer, services):
    from tests.setup.platform import serviceperson

    serviceperson(services)
    serviceperson(services, "sp-demo-2", "Lukas Becker", "+4915100000002")
    services.accounts.add("sales1")
    services.db.rows[("users", "sales1")] = {"role": "sales"}
    people = client.get("/v1/servicepersons", headers=bearer("soneeva", CONSOLE)).json()["servicepersons"]
    assert people[0] == {"uid": "sp-demo-1", "name": "Sanne de Vries", "phone": "+31600000001",
                         "email": "sp-demo-1@example.com", "active": True}
    assert [p["uid"] for p in people] == ["sp-demo-1", "sp-demo-2"]


def test_a_business_sees_only_its_own_servicepersons_contact(client, bearer, services):

    from tests.setup.platform import business, serviceperson

    biz = business(services)
    for uid, name, phone in [("sp-demo-1", "Sanne", "+31600000001"),
                             ("sp-demo-2", "Lukas Becker", "+4915100000002"), ("sp-demo-3", "Amira", "")]:
        serviceperson(services, uid, name, phone)
    headers = lambda sub: bearer("soneeva", [Scope.HUB_SERVICEPERSON_VIEW], sub=sub)  # noqa: E731
    assert client.get("/v1/servicepersons", headers=headers(biz)).json() == {"servicepersons": []}
    services.db.rows[("businesses", biz)]["servicepersonUid"] = "sp-demo-2"
    (card,) = client.get("/v1/servicepersons", headers=headers(biz)).json()["servicepersons"]
    assert card == {"uid": "sp-demo-2", "name": "Lukas Becker", "phone": "+4915100000002",
                    "email": "sp-demo-2@example.com"}
    from tests.test_contract import FIXTURE

    assert set(card) == set(FIXTURE["servicepersonCard"])
    assert len(client.get("/v1/servicepersons", headers=headers("-")).json()["servicepersons"]) == 3


def test_the_console_reads_the_scope_catalogue_without_platform_scopes(client, bearer, services):
    from tests.setup.platform import business

    business(services)
    seeded = services.db.rows[("apps", "soneeva")]["scopes"]
    assert "hub.platform.view" not in seeded and "hub.media.view" in seeded
    headers = bearer("console-like", [Scope.HUB_BUSINESS_VIEW])
    assert client.get("/v1/scopes/soneeva", headers=headers).json()["scopes"][0]["id"] == "overview.view"
    assert client.get("/v1/scopes/soneeva", headers=bearer("bare", [])).status_code == 403



def test_pause_needs_write_and_delete_needs_its_own_scope(created, client, bearer, services):
    url, biz = f"/v1/businesses/{created['id']}", created["id"]
    roles = {"writer": ["Businesses.View", "Businesses.Write"], "deleter": ["Businesses.Delete"]}
    for uid, scopes in roles.items():
        services.db.rows[(Default.ROLES.value, uid)] = {"scopes": scopes}
        services.db.rows[(Default.USERS.value, uid)] = {"uid": uid, "role": uid}
    status = lambda value, **who: client.patch(  # noqa: E731
        url, json={"status": value}, headers=bearer("soneeva", CONSOLE, **who))
    assert status("onhold", uid="writer").json()["status"] == "onhold"
    refused = status("deleted", uid="writer")
    assert refused.status_code == 403 and "Businesses.Delete" in refused.json()["detail"]
    assert status("deleted", sub=biz).status_code == 403  # never the business's own call
    assert status("deleted", uid="deleter").json()["status"] == "deleted"
    audit = [r for (c, _), r in services.db.rows.items() if c == Default.AUDIT.value]
    assert audit[-1]["action"] == "business.delete" and audit[-1]["data"] == {"status": "deleted"}
    assert status("running", uid="writer").status_code == 403  # restoring needs Businesses.Delete too

    listed = lambda query="": client.get(  # noqa: E731
        f"/v1/businesses{query}", headers=bearer("soneeva", CONSOLE)).json()["businesses"]
    assert listed() == [] and [b["id"] for b in listed("?status=deleted")] == [biz]
    home = client.get("/v1/platform/home", headers=bearer("soneeva", CONSOLE, uid="writer"))
    assert home.json()["businesses"]["total"] == 0
