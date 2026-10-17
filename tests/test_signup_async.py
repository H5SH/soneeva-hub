"""Sign-up answers before provisioning; provisioning is idempotent, resumable and reports its failure."""

import asyncio

import pytest

from src.models.db import Default
from tests.test_businesses import CONSOLE
from tests.test_plans import invoke

OWNER = {"email": "rosa@example.com", "password": "secret1", "displayName": "Rosa", "username": "rosa"}
SIGNUP = {"owner": OWNER, "name": "Pizza Roma", "type": "restaurant", "country": "NL",
          "timezone": "Europe/Amsterdam", "address": "Dam 1",
          "hours": {"mon": [{"open": "09:00", "close": "17:00"}]}}


class FakeIdentity:
    """Every account's password is `secret1`."""

    def __init__(self, accounts) -> None:
        self._accounts = accounts

    async def uid(self, email: str, password: str) -> str | None:
        found = [a.uid for a in self._accounts.accounts.values() if a.email == email]
        return found[0] if found and password == "secret1" else None


@pytest.fixture
def signup(client, bearer, services):
    invoke(services, "seed:soneeva")
    services.businesses.identity = FakeIdentity(services.accounts)

    def post(body=SIGNUP):
        return client.post("/v1/businesses", json=body, headers=bearer("soneeva", CONSOLE))

    return post


def status(client, bearer, business_id):
    headers = bearer("soneeva", CONSOLE, sub=business_id)
    return client.get(f"/v1/businesses/{business_id}", headers=headers).json()


def fail_database(services):
    async def broken(name):
        raise RuntimeError("database not ready")

    services.db.create_database = broken


def test_signup_answers_pending_then_provisions_in_the_background(signup, client, bearer, services):
    made = signup()
    assert made.status_code == 201 and made.json()["provisioned"] is False
    business = status(client, bearer, made.json()["id"])
    assert business["provisioned"] is True and business["provisionError"] is None
    bdb = services.business_dbs[business["dbId"]]
    assert bdb.rows[("branches", "main")]["address"] == "Dam 1"
    assert bdb.rows[("branches", "main")]["hours"]["mon"] == [{"open": "09:00", "close": "17:00"}]
    claims = services.accounts.claims[business["ownerUid"]]
    assert claims == {"username": "rosa", "businessId": business["id"]}


def test_a_failed_provisioning_keeps_the_signup_and_is_retried(signup, client, bearer, services):
    working = services.db.create_database
    fail_database(services)
    made = signup()
    assert made.status_code == 201
    business_id, owner = made.json()["id"], made.json()["ownerUid"]
    failed = status(client, bearer, business_id)
    assert failed["provisioned"] is False and failed["provisionError"] == "database not ready"
    assert owner in services.accounts.accounts  # nothing rolled back: the retry finishes it
    services.db.create_database = working
    retry = client.post(f"/v1/businesses/{business_id}/provision",
                        headers=bearer("soneeva", CONSOLE, sub=business_id))
    assert retry.status_code == 202 and retry.json()["provisionError"] is None
    done = status(client, bearer, business_id)
    assert done["provisioned"] is True and done["provisionError"] is None


def test_a_password_signup_resumes_per_email_never_duplicates(signup, client, bearer, services):
    fail_database(services)
    first = signup().json()
    del services.db.create_database  # the fake's own method again
    again = signup()
    assert again.status_code == 201 and again.json()["id"] == first["id"]
    assert status(client, bearer, first["id"])["provisioned"] is True
    assert len(asyncio.run(services.db.get_rows(Default.BUSINESSES))) == 1
    assert signup().status_code == 409  # provisioned: the email is simply taken


def test_a_wrong_password_never_resumes(signup):
    signup()
    wrong = signup(SIGNUP | {"owner": OWNER | {"password": "guessed"}})
    assert wrong.status_code == 409


def test_an_account_left_without_its_business_row_resumes(signup, services):
    asyncio.run(services.accounts.create("rosa@example.com", "secret1", "Rosa", {"username": "rosa"}))
    made = signup()
    assert made.status_code == 201 and len(services.accounts.accounts) == 2  # owner1 and rosa only


def test_provisioning_twice_keeps_what_moved(signup, services):
    business = signup().json()
    bdb = services.business_dbs[business["dbId"]]
    bdb.rows[("checklist", "menu")]["state"] = "done"
    services.db.rows[("businesses", business["id"])]["provisioned"] = False
    asyncio.run(services.businesses.provisioner.run(business["id"]))
    assert bdb.rows[("checklist", "menu")]["state"] == "done"


def test_cli_provisions_then_marks_deleted(signup, services):
    fail_database(services)
    business_id = signup().json()["id"]
    del services.db.create_database
    invoke(services, "business:provision", business_id)
    assert services.db.rows[("businesses", business_id)]["provisioned"] is True
    invoke(services, "business:delete", business_id)
    assert services.db.rows[("businesses", business_id)]["status"] == "deleted"
    assert f"b-{business_id}" in services.db.databases  # the database is the operator's to remove
