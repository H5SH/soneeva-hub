"""The console's store apps: store sign-in, sign-up from a store, connecting and uninstalling."""

import pytest

from src.models.db import Default
from tests.setup.platform import business
from tests.setup.stores import FIXTURE, SCOPES, SIGNUP, STORE, STORE_PATH
from tests.test_businesses import CONSOLE
from tests.test_plans import invoke


def sign_in(client, console, store=STORE):
    return client.post("/v1/stores/sign-in", json=store, headers=console())


def test_a_store_with_no_business_is_not_found(client, console):
    assert sign_in(client, console).status_code == 404


def test_a_store_sign_up_makes_a_passwordless_owner_who_signs_in(client, console, services, signed_up):
    account = services.accounts.accounts[signed_up["ownerUid"]]
    assert account.email == "rosa@example.com" and signed_up["type"] == "shop"
    found = sign_in(client, console).json()
    assert set(found) == set(FIXTURE["storeSignIn"])
    assert set(found["owner"]) == set(FIXTURE["storeSignIn"]["owner"])
    assert (found["businessId"], found["status"]) == (signed_up["id"], "active")
    assert found["owner"]["uid"] == signed_up["ownerUid"]
    assert services.accounts.claims[signed_up["ownerUid"]]["businessId"] == signed_up["id"]


@pytest.mark.parametrize("change", [
    {"store": None},  # no password and no store
    {"owner": SIGNUP["owner"] | {"password": "secret1"}},  # a store's owner has no password
])
def test_an_owner_has_a_password_or_a_store_never_both(client, console, change):
    assert client.post("/v1/businesses", json=SIGNUP | change, headers=console()).status_code == 422


def test_a_store_signs_up_once(client, console, services, signed_up):
    again = SIGNUP | {"owner": SIGNUP["owner"] | {"email": "other@example.com", "username": "other"}}
    assert client.post("/v1/businesses", json=again, headers=console()).status_code == 409
    assert all(a.email != "other@example.com" for a in services.accounts.accounts.values())


def test_an_email_with_an_account_is_refused_and_keeps_its_account(client, console, services):
    services.accounts.add("taken", email="rosa@example.com")
    assert client.post("/v1/businesses", json=SIGNUP, headers=console()).status_code == 409
    assert "taken" in services.accounts.accounts
    assert sign_in(client, console).status_code == 404


def test_a_lost_race_for_the_store_leaves_nothing_behind(client, console, services):
    invoke(services, "seed:soneeva")
    real = services.db.get_row

    async def not_yet(collection, row_id):  # the store is free when checked, taken when claimed
        return None if collection == Default.STORES else await real(collection, row_id)

    services.db.get_row = not_yet
    taken = {"businessId": "other", "platform": "shopify"}
    services.db.rows[("stores", "shopify:rosa.myshopify.com")] = taken
    assert client.post("/v1/businesses", json=SIGNUP, headers=console()).status_code == 409
    assert all(a.email != "rosa@example.com" for a in services.accounts.accounts.values())
    businesses = [r for (c, _), r in services.db.rows.items() if c == "businesses"]
    assert businesses and all(r["isDeleted"] for r in businesses)


def test_uninstall_disables_and_reinstall_gives_the_status_back(client, console, services, signed_up):
    biz = signed_up["id"]
    services.db.rows[("businesses", biz)]["status"] = "running"
    assert client.post(f"/v1/businesses/{biz}/disable", headers=console(biz)).json()["status"] == "deleted"
    uninstalled = services.db.rows[("businesses", biz)]["uninstalled"]
    assert uninstalled["status"] == "running" and uninstalled["at"]
    assert client.post(f"/v1/businesses/{biz}/disable", headers=console(biz)).json()["status"] == "deleted"
    assert services.db.rows[("businesses", biz)]["uninstalled"] == uninstalled  # on a retry too
    assert sign_in(client, console).json()["status"] == "disabled"
    assert client.get("/v1/businesses", headers=console()).json()["businesses"] == []
    assert client.post(f"/v1/businesses/{biz}/enable", headers=console(biz)).json()["status"] == "running"
    assert services.db.rows[("businesses", biz)]["uninstalled"] is None
    assert services.db.rows[("businesses", biz)]["reenabledAt"]
    assert client.post(f"/v1/businesses/{biz}/enable", headers=console(biz)).json()["status"] == "running"
    assert sign_in(client, console).json()["status"] == "active"


def test_a_business_deleted_on_the_platform_is_not_brought_back(client, console, services, signed_up):
    biz = signed_up["id"]
    services.db.rows[("businesses", biz)]["status"] = "deleted"
    assert sign_in(client, console).json()["status"] == "disabled"
    assert client.post(f"/v1/businesses/{biz}/disable", headers=console(biz)).status_code == 200
    assert client.post(f"/v1/businesses/{biz}/enable", headers=console(biz)).status_code == 409


def test_deleting_after_an_uninstall_cannot_be_undone_by_a_reinstall(client, console, services, signed_up):
    biz = signed_up["id"]
    client.post(f"/v1/businesses/{biz}/disable", headers=console(biz))
    url = f"/v1/businesses/{biz}"
    assert client.patch(url, json={"status": "running"}, headers=console()).status_code == 200
    assert client.patch(url, json={"status": "deleted"}, headers=console()).status_code == 200
    assert client.post(f"/v1/businesses/{biz}/enable", headers=console(biz)).status_code == 409


def test_a_disabled_owner_cannot_come_in_from_the_store(client, console, services, signed_up):
    uid, biz = signed_up["ownerUid"], signed_up["id"]
    services.accounts.accounts[uid] = services.accounts.accounts[uid].model_copy(update={"disabled": True})
    assert sign_in(client, console).json()["status"] == "disabled"
    assert client.post(f"/v1/businesses/{biz}/enable", headers=console(biz)).status_code == 409


def test_disable_is_only_for_a_business_with_a_store(client, console, services):
    biz = business(services)
    assert client.post(f"/v1/businesses/{biz}/disable", headers=console(biz)).status_code == 409
    assert services.db.rows[("businesses", biz)]["status"] != "deleted"


def test_only_the_console_reaches_stores(client, bearer, console, signed_up):
    biz = signed_up["id"]
    assert client.post("/v1/stores/sign-in", json=STORE, headers=console(biz)).status_code == 403
    without = lambda: bearer("another-app", CONSOLE)  # noqa: E731
    assert client.post("/v1/stores/sign-in", json=STORE, headers=without()).status_code == 403
    assert client.post(f"/v1/businesses/{biz}/disable", headers=without()).status_code == 403
    assert client.post(f"/v1/businesses/{biz}/enable", headers=console("other")).status_code == 404
    bad = {"platform": "wix", "storeId": "x"}
    assert client.post("/v1/stores/sign-in", json=bad, headers=console()).status_code == 422
    bad = STORE | {"storeId": "a/b"}
    assert client.post("/v1/stores/sign-in", json=bad, headers=console()).status_code == 422


def test_an_owner_connects_and_disconnects_a_store(client, console, services):
    biz = business(services)
    owner = lambda: console(biz, "owner1")  # noqa: E731
    assert client.put(STORE_PATH, headers=owner()).status_code == 204
    assert client.put(STORE_PATH, headers=owner()).status_code == 204  # safe to repeat
    assert sign_in(client, console).json()["businessId"] == biz
    second = "/v1/stores/shopify/two.myshopify.com"
    assert client.put(second, headers=owner()).status_code == 409  # one per platform
    assert client.put("/v1/stores/bigcommerce/abc123", headers=owner()).status_code == 204
    assert client.delete(STORE_PATH, headers=owner()).status_code == 204
    assert sign_in(client, console).status_code == 404
    assert client.delete(STORE_PATH, headers=owner()).status_code == 404
    assert client.put(STORE_PATH, headers=owner()).status_code == 204  # a soft-deleted row is taken over
    assert sign_in(client, console).json()["businessId"] == biz


def test_only_the_owner_connects_a_store(client, console, services, signed_up):
    biz = business(services)
    owner = lambda: console(biz, "owner1")  # noqa: E731
    assert client.put(STORE_PATH, headers=owner()).status_code == 409  # signed_up's store
    assert client.delete(STORE_PATH, headers=owner()).status_code == 404
    bdb = services.business_dbs[f"b-{biz}"]
    bdb.rows[("roles", "staff")] = {"scopes": ["connections.edit"]}
    bdb.rows[("users", "clerk")] = {"role": "staff"}
    assert client.put("/v1/stores/bigcommerce/abc123", headers=console(biz, "clerk")).status_code == 403
    assert client.delete("/v1/stores/bigcommerce/abc123", headers=console(biz, "clerk")).status_code == 403
    assert client.put("/v1/stores/bigcommerce/abc123", headers=console("-", "owner1")).status_code == 403


def test_an_owner_whose_account_is_gone_can_still_be_uninstalled(client, console, services, signed_up):
    biz = signed_up["id"]
    del services.accounts.accounts[signed_up["ownerUid"]]
    found = sign_in(client, console).json()
    assert found["status"] == "disabled" and found["owner"] is None
    assert client.post(f"/v1/businesses/{biz}/disable", headers=console(biz)).status_code == 200
    assert client.post(f"/v1/businesses/{biz}/enable", headers=console(biz)).status_code == 409


def test_an_operator_frees_a_store_stuck_on_a_dead_business(client, console, services, signed_up):
    services.db.rows[("businesses", signed_up["id"])]["status"] = "deleted"
    invoke(services, "store:release", "shopify", STORE["storeId"])
    assert sign_in(client, console).status_code == 404


def test_a_failed_store_claim_leaves_no_business_behind(client, console, services, monkeypatch):
    invoke(services, "seed:soneeva")

    async def broken(*_):
        raise RuntimeError("Firestore is down")

    monkeypatch.setattr("src.services.signup.claim_store", broken)
    with pytest.raises(RuntimeError):
        client.post("/v1/businesses", json=SIGNUP, headers=console())
    assert all(a.email != "rosa@example.com" for a in services.accounts.accounts.values())
    assert all(r["isDeleted"] for (c, _), r in services.db.rows.items() if c == "businesses")


def test_the_two_sign_up_conflicts_can_be_told_apart(client, console, signed_up):
    other = SIGNUP | {"store": STORE | {"storeId": "other.myshopify.com"}}
    email = client.post("/v1/businesses", json=other, headers=console())
    assert (email.status_code, email.json()["code"]) == (409, "email-exists")
    store = client.post("/v1/businesses", json=SIGNUP, headers=console())
    assert (store.status_code, store.json()["code"]) == (409, "store_taken")


def test_the_owner_role_holds_the_connection_scopes(services):
    bdb = services.business_dbs[f"b-{business(services)}"]
    assert {"connections.view", "connections.edit"} <= set(bdb.rows[("roles", "owner")]["scopes"])


def test_staging_and_live_each_have_their_own_business_for_a_store(client, bearer, console, signed_up):
    staging = lambda: bearer("soneeva_staging", SCOPES)  # noqa: E731
    assert client.post("/v1/stores/sign-in", json=STORE, headers=staging()).status_code == 404
    second = SIGNUP | {"owner": SIGNUP["owner"] | {"email": "test@example.com", "username": "tester"}}
    made = client.post("/v1/businesses", json=second, headers=staging())
    assert made.status_code == 201 and made.json()["test"] is True
    found = client.post("/v1/stores/sign-in", json=STORE, headers=staging()).json()
    assert found["businessId"] == made.json()["id"]
    assert sign_in(client, console).json()["businessId"] == signed_up["id"]


def test_store_sign_in_retries_a_business_not_provisioned(client, console, services, signed_up):
    biz = signed_up["id"]
    services.db.rows[("businesses", biz)] |= {"provisioned": False, "provisionError": "boom"}
    assert sign_in(client, console).json()["provisioned"] is False
    assert services.db.rows[("businesses", biz)]["provisioned"] is True
