"""/v1/platform/home: each section over what the person reaches, and left out without its scope."""

import pytest

from src.models.admin import Scope
from tests.setup.platform import home_data

SECTIONS = {"businesses", "assignment", "workspace", "people", "advertisements", "payments", "demo"}


@pytest.fixture
def seeded(services):
    return home_data(services)


def home(client, bearer, uid: str, app: str = "karbon-admin", scopes: list | None = None, sub: str = "-"):
    return client.get("/v1/platform/home", headers=bearer(app, scopes or list(Scope), sub=sub, uid=uid))


def test_the_superadmin_sees_every_section(seeded, client, bearer):
    seen = home(client, bearer, "admin").json()
    assert set(seen) == SECTIONS
    assert seen["businesses"]["reach"] == "all" and seen["businesses"]["total"] == 2
    assert seen["assignment"]["unassigned"] == 1
    sanne = {"uid": "sp-demo-1", "name": "Sanne de Vries", "businesses": 1}
    assert sanne in seen["assignment"]["servicepersons"]
    assert seen["workspace"] == {"businesses": []}
    assert seen["people"]["total"] >= 4 and seen["people"]["reach"] == "all"
    assert seen["advertisements"]["ads"][0] | {"id": ""} == {
        "id": "", "title": "Summer", "active": True, "businesses": 2, "dismissed": 0}
    (app,) = seen["payments"]["apps"]
    assert app["subscribers"] == 1 and app["plans"]["active"] >= 1
    assert seen["payments"]["recent"][0]["businessId"] == seeded
    assert not {"amount", "currency"} & set(seen["payments"]["recent"][0])
    assert sum(d["calls"] for d in seen["demo"]["days"]) == 1 and "total" not in seen["demo"]["days"][0]


def test_an_assigned_only_serviceperson_sees_only_their_businesses(seeded, client, bearer):
    seen = home(client, bearer, "sp-demo-1").json()
    assert set(seen) == {"businesses", "workspace"}
    assert seen["businesses"]["reach"] == "assigned" and seen["businesses"]["total"] == 1
    assert [r["id"] for r in seen["businesses"]["recent"]] == [seeded]
    (row,) = seen["workspace"]["businesses"]
    assert row["id"] == seeded and row["meetingDone"] is False and row["done"] == 1 < row["total"]
    listed = client.get("/v1/businesses", headers=bearer("karbon-admin", list(Scope), uid="sp-demo-1"))
    assert [b["id"] for b in listed.json()["businesses"]] == [seeded]


@pytest.mark.parametrize(("scopes", "section"), [
    (["Users.View"], "people"),
    (["Advertise.View"], "advertisements"),
    (["Assigned.Chats.View"], "demo"),
    ([str(Scope.APP_VIEW)], "payments"),
    (["Businesses.View"], "businesses"),
])
def test_each_section_needs_its_scope(seeded, client, bearer, services, scopes, section):
    services.db.rows[("roles", "user")] = {"scopes": scopes}
    assert set(home(client, bearer, "u1").json()) == {section}


def test_the_assigned_twins_reach_only_the_people_looked_after(seeded, client, bearer, services):
    services.db.rows[("roles", "user")] = {"scopes": ["Assigned.Users.View", "Assigned.Chats.View"]}
    services.db.rows[("users", "u2")]["role"] = "user"
    services.db.rows[("assignments", "u2_u1")] = {"servicepersonUid": "u2", "userUid": "u1"}
    seen = home(client, bearer, "u2").json()
    assert seen["people"]["total"] == 1 and seen["people"]["reach"] == "assigned"
    assert sum(d["calls"] for d in seen["demo"]["days"]) == 1


def test_the_app_scopes_and_a_person_are_needed(seeded, client, bearer):
    narrow = home(client, bearer, "admin", app="narrow", scopes=[Scope.HUB_BUSINESS_VIEW]).json()
    assert set(narrow) == {"businesses", "assignment", "workspace"}
    assert home(client, bearer, "admin", sub=seeded).status_code == 403
    no_one = client.get("/v1/platform/home", headers=bearer("karbon-admin", list(Scope)))
    assert no_one.status_code == 403
