"""/v1/roles: console ids and payment scopes on one role; edits apply on the next request."""

import pytest

from src.models.admin import Scope
from tests.setup.people import as_, people

URL = "/v1/roles"


@pytest.fixture
def platform(services):
    people(services)
    return services


def test_the_list_has_holders_the_catalogue_and_what_the_caller_may_give(platform, client, bearer):
    listed = client.get(URL, headers=as_(bearer, "admin")).json()
    by_name = {r["name"]: r for r in listed["roles"]}
    assert by_name["user"]["holders"] == 2 and by_name["serviceperson"]["holders"] == 1
    ids = {s["id"] for s in listed["scopes"]}
    assert {"Users.View", "Roles.Write", "payment.app.view", "platform.docs.view"} <= ids
    assert {"id": "Users.View", "group": "people", "twin": True} in listed["scopes"]
    assert not any(s.startswith("hub.") for s in ids)
    assert set(listed["assignable"]) == {"superadmin", "serviceperson", "user"}
    assert client.get(URL, headers=as_(bearer, "u1")).status_code == 403


def test_create_save_and_delete_mixing_console_and_payment_scopes(platform, client, bearer):
    def admin():
        return as_(bearer, "admin")

    mixed = ["Users.View", "payment.app.view"]
    made = client.post(URL, json={"name": "Auditor", "scopes": mixed}, headers=admin())
    assert made.status_code == 201, made.text
    assert {"name": "auditor", "scopes": mixed, "holders": 0} in made.json()["roles"]
    assert client.post(URL, json={"name": "auditor"}, headers=admin()).status_code == 409
    assert client.post(URL, json={"name": "x", "scopes": ["Nope.Invent"]}, headers=admin()).status_code == 422
    twin = {"scopes": ["Assigned.Users.View"]}
    saved = client.put(f"{URL}/serviceperson", json=twin, headers=admin()).json()
    assert {"name": "serviceperson", "scopes": ["Assigned.Users.View"], "holders": 1} in saved["roles"]
    assert client.put(f"{URL}/nope", json={"scopes": []}, headers=admin()).status_code == 404
    assert client.delete(f"{URL}/user", headers=admin()).status_code == 409
    left = client.delete(f"{URL}/auditor", headers=admin()).json()["roles"]
    assert "auditor" not in {r["name"] for r in left}


def test_nobody_gives_what_they_lack_or_edits_their_own_role(platform, client, bearer):
    platform.db.rows[("roles", "editor")] = {"scopes": ["Roles.Write", "Users.View"]}
    platform.db.rows[("users", "ed")] = {"role": "editor"}
    platform.accounts.add("ed", username="ed")
    def ed():
        return as_(bearer, "ed")

    def make(name: str, scope: str) -> int:
        return client.post(URL, json={"name": name, "scopes": [scope]}, headers=ed()).status_code

    assert make("keys", str(Scope.KEY_ROTATE)) == 403
    assert make("star", "*") == 403
    assert make("viewers", "Users.View") == 201
    assert client.put(f"{URL}/editor", json={"scopes": ["*"]}, headers=ed()).status_code == 403
    assert client.delete(f"{URL}/editor", headers=ed()).status_code == 403


def test_an_edited_role_applies_on_the_next_request(platform, client, bearer):
    assert client.get("/v1/people/u2", headers=as_(bearer, "u1")).status_code == 404
    client.put(f"{URL}/user", json={"scopes": ["Users.View"]}, headers=as_(bearer, "admin"))
    assert client.get("/v1/people/u2", headers=as_(bearer, "u1")).status_code == 200


def test_taking_scopes_off_needs_nothing_and_hub_ids_never_stay(platform, client, bearer):
    rows = platform.db.rows
    rows[("roles", "editor")] = {"scopes": ["Roles.Write"]}
    rows[("roles", "ops")] = {"scopes": ["Users.View", "payment.key.rotate", "hub.business.view"]}
    rows[("users", "ed")] = {"role": "editor"}
    platform.accounts.add("ed", username="ed")
    def ed():
        return as_(bearer, "ed")

    listed = {r["name"]: r["scopes"] for r in client.get(URL, headers=ed()).json()["roles"]}
    assert listed["ops"] == ["Users.View", "payment.key.rotate"]
    kept = {"scopes": ["payment.key.rotate", "hub.number.buy"]}
    assert client.put(f"{URL}/ops", json=kept, headers=ed()).status_code == 200
    assert rows[("roles", "ops")]["scopes"] == ["payment.key.rotate"]
    assert client.put(f"{URL}/ops", json={"scopes": ["Users.View"]}, headers=ed()).status_code == 403
    assert client.put(f"{URL}/ops", json={"scopes": []}, headers=ed()).status_code == 200
    assert client.delete(f"{URL}/ops", headers=ed()).status_code == 200
