"""/v1/people: one record per platform person; nobody grants what they lack or edits themselves upward."""

import pytest

from tests.setup.people import as_, assignments, people

URL = "/v1/people"
NEW = {"email": "New@Example.com", "displayName": "New", "username": "newbie"}


@pytest.fixture
def platform(services):
    people(services)
    return services


def test_the_app_scope_and_a_named_person_are_required(platform, client, bearer):
    assert client.get(f"{URL}/u1", headers=bearer("bare", [], uid="admin")).status_code == 403
    assert client.get(f"{URL}/u1", headers=as_(bearer, "admin")).status_code == 200
    assert client.get(f"{URL}/member", headers=as_(bearer, "admin")).status_code == 404
    assert client.get(f"{URL}/u2", headers=as_(bearer, "u1")).status_code == 404
    assert client.get(f"{URL}/u1", headers=as_(bearer, "u1")).status_code == 200


def test_the_list_joins_role_tokens_apps_and_who_looks_after_them(platform, client, bearer):
    platform.db.rows[("users", "u2")] |= {"apps": ["soneeva"], "businesses": ["b1"]}
    listed = client.get(URL, headers=as_(bearer, "admin")).json()
    by_uid = {p["uid"]: p for p in listed["people"]}
    assert "member" not in by_uid and listed["nextPageToken"] is None
    assert by_uid["u1"]["servicepersons"] == ["sp"] and by_uid["u1"]["tokens"] == 30
    assert by_uid["u2"]["apps"] == ["soneeva"] and by_uid["u2"]["businesses"] == ["b1"]


def test_create_without_a_password_returns_a_link_once(platform, client, bearer):
    made = client.post(URL, json=NEW | {"role": "user", "apps": ["soneeva"]}, headers=as_(bearer, "admin"))
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["link"] == "https://reset.example/new@example.com"
    assert body["person"]["email"] == "new@example.com" and body["person"]["apps"] == ["soneeva"]
    assert platform.db.rows[("users", body["person"]["uid"])]["role"] == "user"
    taken = client.post(URL, json=NEW | {"password": "secret1"}, headers=as_(bearer, "admin"))
    assert taken.status_code == 409


def test_an_assigned_creator_looks_after_the_new_person_and_pays_their_tokens(platform, client, bearer):
    body = NEW | {"password": "secret1", "tokens": 40, "role": "superadmin"}
    made = client.post(URL, json=body, headers=as_(bearer, "sp"))
    assert made.status_code == 201, made.text
    person = made.json()["person"]
    assert made.json()["link"] is None
    assert person["role"] == "user" and person["servicepersons"] == ["sp"] and person["tokens"] == 40
    assert platform.db.rows[("users", "sp")]["tokens"] == 60
    assert client.post(URL, json=NEW | {"email": "x@example.com", "apps": ["soneeva"]},
                       headers=as_(bearer, "sp")).status_code == 403


def test_overspending_rolls_the_account_back(platform, client, bearer):
    made = client.post(URL, json=NEW | {"tokens": 500}, headers=as_(bearer, "sp"))
    assert made.status_code == 409
    assert all(a.email != "new@example.com" for a in platform.accounts.accounts.values())


def test_nobody_gives_a_role_or_apps_they_lack(platform, client, bearer):
    platform.db.rows[("roles", "serviceperson")]["scopes"].append("Users.Role.Write")
    platform.db.rows[("roles", "helper")] = {"scopes": ["Assigned.Users.View"]}
    platform.db.rows[("users", "sp")]["apps"] = ["soneeva"]
    url = f"{URL}/u1"
    assert client.patch(url, json={"role": "superadmin"}, headers=as_(bearer, "sp")).status_code == 403
    assert client.patch(url, json={"role": "nope"}, headers=as_(bearer, "sp")).status_code == 422
    assert client.patch(url, json={"role": "helper"}, headers=as_(bearer, "sp")).status_code == 200
    assert client.patch(url, json={"apps": ["other"]}, headers=as_(bearer, "sp")).status_code == 403
    given = client.patch(url, json={"apps": ["soneeva"]}, headers=as_(bearer, "sp")).json()
    assert given["person"]["apps"] == ["soneeva"]


def test_nobody_edits_themselves_into_more_power(platform, client, bearer):
    mine = f"{URL}/admin"
    for change in ({"role": "user"}, {"apps": ["x"]}, {"businesses": ["b"]}, {"disabled": True}):
        assert client.patch(mine, json=change, headers=as_(bearer, "admin")).status_code == 403, change
    assert client.delete(mine, headers=as_(bearer, "admin")).status_code == 403


def test_people_change_their_own_name_and_password_with_profile_write(platform, client, bearer):
    mine = {"displayName": "Me", "password": "secret22"}
    assert client.patch(f"{URL}/u1", json=mine, headers=as_(bearer, "u1")).status_code == 403
    platform.db.rows[("roles", "user")] = {"scopes": ["Profile.Write"]}
    assert client.patch(f"{URL}/u1", json=mine, headers=as_(bearer, "u1")).status_code == 200
    assert platform.accounts.passwords["u1"] == "secret22"


def test_disabling_reaches_sign_in_and_the_record(platform, client, bearer):
    off = client.patch(f"{URL}/u1", json={"disabled": True}, headers=as_(bearer, "admin")).json()["person"]
    assert off["disabled"] and platform.db.rows[("users", "u1")]["disabled"] is True


def test_leaving_a_serviceperson_role_drops_their_assignments(platform, client, bearer):
    changed = client.patch(f"{URL}/sp", json={"role": "user"}, headers=as_(bearer, "admin"))
    assert changed.json()["dropped"] == 1 and assignments(platform) == set()


def test_deleting_refunds_the_giver_and_removes_the_record(platform, client, bearer):
    assert client.delete(f"{URL}/u2", headers=as_(bearer, "sp")).status_code == 403
    gone = client.delete(f"{URL}/u1", headers=as_(bearer, "sp")).json()
    assert gone == {"deleted": True, "refunded": 30}
    assert platform.db.rows[("users", "sp")]["tokens"] == 130 and "u1" not in platform.accounts.accounts
    assert ("users", "u1") not in platform.db.rows and assignments(platform) == set()


def test_tokens_and_assignments(platform, client, bearer):
    put = client.put(f"{URL}/u1/tokens", json={"tokens": None}, headers=as_(bearer, "sp"))
    assert put.status_code == 403  # a limited giver cannot give no limit
    given = client.put(f"{URL}/u1/tokens", json={"tokens": 50}, headers=as_(bearer, "sp"))
    assert given.json()["tokens"] == 50
    assert platform.db.rows[("users", "sp")]["tokens"] == 80

    def assign(uid: str):
        body = {"servicepersons": [uid]}
        return client.put(f"{URL}/u2/servicepersons", json=body, headers=as_(bearer, "admin"))

    assert assign("sp").json()["servicepersons"] == ["sp"]
    assert assign("u1").status_code == 422


def test_password_reset_sends_or_links(platform, client, bearer):
    url = f"{URL}/u1/password-reset"
    assert client.post(url, json={"send": True}, headers=as_(bearer, "sp")).json() == {"sent": True}
    assert client.post(url, json={}, headers=as_(bearer, "admin")).json()["link"].startswith("https://")
    assert client.post(url, json={"send": True}, headers=as_(bearer, "u2")).status_code == 403
