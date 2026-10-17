"""The /v1 contract: field names the console reads (tests/contract/hub.json, shared with it)."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.models.admin import DEFAULT_ROLES, Scope
from src.models.platform import BusinessCreate
from src.services.catalogue import read_console_scopes, seed_platform_roles
from src.services.people.rules import HUB, known
from src.tunables import tunable
from tests.setup.platform import business, home_data, serviceperson

FIXTURE = json.loads((Path(__file__).parent / "contract" / "hub.json").read_text())
CONSOLE_SCOPES = Path(__file__).parents[2] / "dashboard" / "scopes.json"


@pytest.fixture
def live(client, bearer, services):
    """A subscribed business with a number, and the console's headers for it."""
    biz = business(services, subscribed=True)
    services.db.rows[("businesses", biz)]["voiceUrl"] = "https://voice.example"
    serviceperson(services)
    headers = lambda sub="-": bearer("soneeva", list(Scope), sub=sub)  # noqa: E731
    bought = client.post(
        f"/v1/businesses/{biz}/numbers", json=FIXTURE["bought"] | {"sid": None}, headers=headers(biz)
    )
    assert bought.status_code == 201, bought.text
    return biz, headers, bought.json()


def same_keys(actual: dict, example: dict) -> None:
    assert set(actual) == set(example)
    for key, value in example.items():
        if isinstance(value, dict):
            same_keys(actual[key], value)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            same_keys(actual[key][0], value[0])


def test_business_shapes(live, client):
    biz, headers, bought = live
    same_keys(bought, FIXTURE["bought"])
    same_keys(client.get(f"/v1/businesses/{biz}", headers=headers(biz)).json(), FIXTURE["business"])
    (listed,) = client.get("/v1/businesses", headers=headers()).json()["businesses"]
    same_keys(listed, FIXTURE["business"])
    assert listed["plan"] == {"code": "per-minute", "status": "active", "active": True}


def test_people_numbers_plans_and_voice(live, client):
    biz, headers, _ = live
    people = client.get("/v1/servicepersons", headers=headers()).json()["servicepersons"]
    same_keys(people[0], FIXTURE["serviceperson"])
    numbers = client.get(f"/v1/businesses/{biz}/numbers?country=NL", headers=headers(biz)).json()
    same_keys(numbers["numbers"][0], FIXTURE["number"])
    same_keys(client.get("/v1/public/plans?app=soneeva").json()["plans"][0], FIXTURE["plan"])
    saved = client.post(
        f"/v1/businesses/{biz}/voice/settings", json=FIXTURE["voiceSettings"], headers=headers(biz)
    ).json()
    same_keys(saved, FIXTURE["voiceSaved"])


def test_home_shape(client, bearer, services):
    home_data(services)
    get = lambda uid: client.get(  # noqa: E731
        "/v1/platform/home", headers=bearer("karbon-admin", list(Scope), uid=uid)).json()
    same_keys(get("admin") | {"workspace": get("sp-demo-1")["workspace"]}, FIXTURE["home"])


def test_the_console_signup_body_is_accepted():
    body = BusinessCreate(**FIXTURE["signup"])
    assert set(FIXTURE["signup"]) <= set(BusinessCreate.model_fields)
    assert (body.branchCount, body.address) == (1, "Dam 1, Amsterdam")


def test_a_business_token_may_go_live_but_not_assign(live, client):
    biz, headers, _ = live
    url = f"/v1/businesses/{biz}"
    live_now = {"configured": True, "status": "running"}
    assert client.patch(url, json=live_now, headers=headers(biz)).status_code == 200
    assert client.patch(url, json={"servicepersonUid": "sp-demo-1"}, headers=headers(biz)).status_code == 403
    assert client.patch(url, json={"servicepersonUid": "sp-demo-1"}, headers=headers()).status_code == 200


@pytest.mark.skipif(not CONSOLE_SCOPES.exists(), reason="console repo not beside this one")
def test_seeded_scopes_are_the_console_scopes():
    console = read_console_scopes(CONSOLE_SCOPES)
    assert tunable("scopes.soneeva") == console
    ids = {s["id"] for s in console}
    for role, row in tunable("businesses.roles").items():
        assert set(row["scopes"]) <= ids | {"*"}, role
    groups = json.loads(CONSOLE_SCOPES.read_text()).get("platform")
    if groups is None:
        return  # platform pages left the console; their ids are the internal app's
    platform = {s["id"] for g in groups for s in g["scopes"]}
    for role, scopes in tunable("platformRoles").items():
        assert set(scopes) <= platform | HUB | {"*"}, role


def test_missing_roles_are_created_with_their_defaults(services):
    db = services.db
    asyncio.run(seed_platform_roles(db))
    scopes = db.rows[("roles", "serviceperson")]["scopes"]
    assert "Businesses.Access" in scopes and str(Scope.APP_VIEW) in scopes
    assert "Businesses.View" not in scopes
    assert not any(s.startswith("hub.") for s in scopes)
    assert set(DEFAULT_ROLES) <= {k for (c, k) in db.rows if c == "roles"}


def test_seeding_never_adds_scopes_back_to_an_existing_role(services):
    db = services.db
    db.rows[("roles", "serviceperson")] = {"scopes": ["Businesses.Access", "Profile.Write"]}
    db.rows[("roles", "superadmin")] = {"scopes": ["*"]}
    written = asyncio.run(seed_platform_roles(db))
    assert "serviceperson" not in written and "superadmin" not in written
    assert db.rows[("roles", "serviceperson")]["scopes"] == ["Businesses.Access", "Profile.Write"]
    assert db.rows[("roles", "superadmin")]["scopes"] == ["*"]
    assert "sales" in written


def test_seeding_takes_every_app_scope_off_every_role(services):
    db = services.db
    db.rows[("roles", "superadmin")] = {"scopes": ["*", "hub.people.view", "payment.admin"]}
    db.rows[("roles", "custom")] = {"scopes": ["Users.View", "hub.business.view"]}
    assert "custom" in asyncio.run(seed_platform_roles(db))
    assert db.rows[("roles", "custom")]["scopes"] == ["Users.View"]
    assert not any(s.startswith("hub.") for s in db.rows[("roles", "superadmin")]["scopes"])
    assert asyncio.run(seed_platform_roles(db)) == []

def test_people_roles_and_call_shapes(client, bearer, services):
    from tests.setup.people import as_, people

    people(services)
    services.db.rows[("callers/web-u1/calls", "c0")] = {
        "caller_id": "web-u1",
        "status": "completed",
        "started_at": datetime(2026, 10, 1, tzinfo=UTC),
        "cost": {"total": 0.5},
    }
    services.db.rows[("callers/web-u1/calls/c0/messages", "0")] = {"index": 0, "content": "hi"}
    admin = lambda: as_(bearer, "admin")  # noqa: E731
    same_keys(client.get("/v1/people", headers=admin()).json(), FIXTURE["people"])
    created = client.post("/v1/people", json=FIXTURE["personCreate"] | {"role": "user"}, headers=admin())
    assert created.status_code == 201, created.text
    same_keys(created.json(), FIXTURE["personCreated"])
    uid = created.json()["person"]["uid"]
    patch = FIXTURE["personPatch"] | {"role": "user"}
    updated = client.patch(f"/v1/people/{uid}", json=patch, headers=admin())
    same_keys(updated.json(), FIXTURE["personUpdated"])
    same_keys(client.get(f"/v1/people/{uid}", headers=admin()).json(), FIXTURE["person"])
    same_keys(client.get("/v1/roles", headers=admin()).json(), FIXTURE["roles"])
    made = client.post("/v1/roles", json=FIXTURE["roleCreate"], headers=admin())
    assert made.status_code == 201, made.text
    put = client.put("/v1/roles/auditor", json=FIXTURE["rolePut"], headers=admin())
    same_keys(put.json(), FIXTURE["roles"])
    calls = client.get("/v1/platform/calls?scope=Chats.View", headers=admin()).json()
    same_keys(calls, FIXTURE["calls"])
    message = client.get("/v1/platform/calls/web-u1/c0/messages?scope=Chats.View", headers=admin()).json()
    same_keys(message["messages"][0], FIXTURE["message"])
    same_keys(
        client.get("/v1/platform/calls/totals?scope=Chats.View", headers=admin()).json(), FIXTURE["totals"]
    )
    daily = client.get("/v1/platform/calls/daily?scope=Chats.View", headers=admin()).json()
    same_keys(daily, FIXTURE["callsDaily"])


def test_media_self_and_reset_shapes(client, bearer, services):
    from tests.setup.people import people

    people(services)
    services.db.rows[("roles", "user")] = {"scopes": ["Profile.Write"]}
    scopes = [Scope.HUB_MEDIA_UPDATE, Scope.HUB_ACCOUNT_RECOVER, Scope.HUB_PEOPLE_UPDATE]
    headers = lambda: bearer("console-shapes", scopes, uid="u1")  # noqa: E731
    saved = client.put("/v1/media/users/u1/avatar", content=b"\x89PNG\r\n\x1a\n",
                       headers=headers() | {"Content-Type": "image/png"})
    same_keys(saved.json(), FIXTURE["mediaSaved"])
    same_keys(client.delete("/v1/media/users/u1/avatar", headers=headers()).json(), FIXTURE["mediaDeleted"])
    patched = client.patch("/v1/people/u1", json=FIXTURE["selfPatch"], headers=headers())
    same_keys(patched.json(), FIXTURE["personUpdated"])
    asked = client.post("/v1/accounts/password-reset-request", json=FIXTURE["passwordResetRequest"],
                        headers=headers())
    assert asked.status_code == 202
    same_keys(asked.json(), FIXTURE["passwordResetAccepted"])


def test_branches_shape_and_the_role_catalogue(client, bearer, services):
    from tests.setup.platform import business

    biz = business(services)
    headers = lambda: bearer("shapes", [Scope.HUB_BUSINESS_VIEW, Scope.HUB_PLATFORM_VIEW])  # noqa: E731
    same_keys(client.get(f"/v1/businesses/{biz}/branches", headers=headers()).json(), FIXTURE["branches"])
    for role, scopes in tunable("platformRoles").items():
        assert set(scopes) <= known(), role
