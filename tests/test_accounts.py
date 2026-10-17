"""/v1/accounts: a business's members and each person's own account, as the console uses them."""

import pytest

from src.models.admin import Scope
from src.models.db import Default
from tests.setup.people import as_, people

NEW = {"email": "new@example.com", "password": "secret1", "displayName": "New", "username": "newbie"}


@pytest.fixture
def platform(services):
    people(services)
    services.db.rows[(Default.BUSINESSES.value, "biz1")] = {"name": "B", "dbId": "b-biz1"}
    services.business_dbs.setdefault("b-biz1", type(services.db)())
    return services


def test_the_app_scope_and_a_named_person_are_required(platform, client, bearer):
    assert client.get("/v1/accounts/u1", headers=bearer("bare", [], uid="admin")).status_code == 403
    assert (
        client.get("/v1/accounts/u1", headers=bearer("soneeva", [Scope.HUB_ACCOUNT_VIEW])).status_code == 403
    )
    assert client.get("/v1/accounts/u1", headers=as_(bearer, "admin")).status_code == 403  # /v1/people


def test_members_read_themselves_not_other_businesses(platform, client, bearer):
    me = client.get("/v1/accounts/member", headers=as_(bearer, "member", sub="biz1"))
    assert me.status_code == 200 and me.json()["businessId"] == "biz1"
    assert client.get("/v1/accounts/u1", headers=as_(bearer, "member", sub="biz1")).status_code == 404


def test_a_business_token_manages_its_own_members_with_team_edit(platform, client, bearer, services):
    services.db.rows[(Default.BUSINESSES.value, "biz1")] = {"name": "B", "dbId": "b-biz1"}
    bdb = services.business_dbs.setdefault("b-biz1", type(services.db)())
    bdb.rows[("roles", "owner")] = {"scopes": ["team.view", "team.edit"]}
    bdb.rows[("roles", "staff")] = {"scopes": ["team.view"]}
    bdb.rows[("users", "boss")] = {"role": "owner"}
    bdb.rows[("users", "clerk")] = {"role": "staff"}
    bdb.rows[("grants", "sp")] = {"scopes": ["team.edit"], "blocked": False, "until": None}
    made = client.post("/v1/accounts", json=NEW, headers=as_(bearer, "boss", sub="biz1"))
    assert made.status_code == 201 and made.json()["businessId"] == "biz1"
    assert (
        client.post(
            "/v1/accounts", json=NEW | {"email": "x@example.com"}, headers=as_(bearer, "clerk", sub="biz1")
        ).status_code
        == 403
    )
    assert client.delete("/v1/accounts/u1", headers=as_(bearer, "sp", sub="biz1")).status_code == 404
    assert client.delete("/v1/accounts/member", headers=as_(bearer, "sp", sub="biz1")).status_code == 200
    bdb.rows[("grants", "sp")]["blocked"] = True
    assert (
        client.get(f"/v1/accounts/{made.json()['uid']}", headers=as_(bearer, "sp", sub="biz1")).status_code
        == 403
    )


def test_a_reset_request_is_always_accepted_and_limited_per_email(platform, client, bearer):
    headers = lambda: bearer("recover", [Scope.HUB_ACCOUNT_RECOVER])  # noqa: E731
    url = "/v1/accounts/password-reset-request"
    emails = ("u1@example.com", "nobody@example.com", "U1@example.com", "u1@example.com", "u1@example.com")
    for email in emails:
        answer = client.post(url, json={"email": email}, headers=headers())
        assert answer.status_code == 202 and answer.json() == {"accepted": True}
    assert platform.accounts.sent.count("u1@example.com") == 3
    assert client.post(url, json={"email": "u1@example.com"}, headers=as_(bearer, "admin")).status_code == 403


def test_members_change_their_own_name_and_password_with_profile_edit(platform, client, bearer, services):
    services.db.rows[(Default.BUSINESSES.value, "biz1")] = {"name": "B", "dbId": "b-biz1"}
    bdb = services.business_dbs.setdefault("b-biz1", type(services.db)())
    bdb.rows[("roles", "staff")] = {"scopes": ["profile.edit"]}
    bdb.rows[("users", "member")] = {"role": "staff"}
    mine = {"displayName": "Me", "password": "secret22"}
    url = "/v1/accounts/member"
    assert client.patch(url, json=mine, headers=as_(bearer, "member", sub="biz1")).status_code == 200
    member = as_(bearer, "member", sub="biz1")
    assert client.patch(url, json={"email": "x@example.com"}, headers=member).status_code == 403
    bdb.rows[("roles", "staff")] = {"scopes": ["team.edit"]}
    bdb.rows[("users", "boss")] = {"role": "staff"}
    boss = as_(bearer, "boss", sub="biz1")
    assert client.patch(url, json={"password": "taken123"}, headers=boss).status_code == 403
