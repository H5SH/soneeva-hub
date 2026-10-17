"""Names, tags and blocks for the numbers that call a business: who may read and change them, search,
numbers that never called, unblocking and the audit."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.models.admin import Scope
from src.models.db import BusinessDb
from tests.setup.platform import business
from tests.test_contract import same_keys

FIXTURE = json.loads((Path(__file__).parent / "contract" / "hub.json").read_text())
APP = [Scope.HUB_BUSINESS_VIEW, Scope.HUB_BUSINESS_UPDATE]
JAN, PIET, ANNA, NEW = "+31612345678", "+31613000000", "+4915112345678", "+31687654321"


def at(day: int) -> datetime:
    return datetime(2026, 10, day, 12, tzinfo=UTC)


@pytest.fixture
def biz(services):
    """Three callers and a withheld one; clerk may read (callers.view), temp may not."""
    biz = business(services, subscribed=True)
    bdb = services.business_dbs[f"b-{biz}"]
    for phone, doc in {
        JAN: {"phone": JAN, "branchId": "main", "lastCallAt": at(3), "name": "Jan de Vries",
              "nameLower": "jan de vries", "tags": ["vip"]},
        PIET: {"phone": PIET, "branchId": "main", "lastCallAt": at(2)},
        ANNA: {"phone": ANNA, "lastCallAt": at(1), "tags": ["vip", "nuts"]},
        "anon-CA1": {"phone": None, "anonymous": True, "lastCallAt": at(4)},
    }.items():
        bdb.rows[("callers", phone)] = doc
    for n in range(3):
        bdb.rows[(f"callers/{JAN}/calls", f"c{n}")] = {}
    bdb.rows[("roles", "clerk")] = {"scopes": ["callers.view"]}
    bdb.rows[("roles", "temp")] = {"scopes": ["calls.view"]}
    for uid in ("clerk", "temp"):
        bdb.rows[("users", uid)] = {"role": uid}
    services.db.rows[("users", "boss")] = {"role": "superadmin"}
    services.db.rows[("users", "seller")] = {"role": "sales"}
    return biz


@pytest.fixture
def call(client, bearer):
    def run(method: str, path: str, uid: str, sub: str = "-", **kwargs):
        return client.request(method, path, headers=bearer("console", APP, sub=sub, uid=uid), **kwargs)

    return run


def callers(services, biz: str) -> dict[str, dict]:
    return {p: d for (c, p), d in services.business_dbs[f"b-{biz}"].rows.items() if c == "callers"}


def phones(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [c["phone"] for c in response.json()["callers"]]


def test_callers_are_listed_newest_first_without_withheld_numbers(biz, call):
    url = f"/v1/businesses/{biz}/callers"
    assert phones(call("GET", url, "owner1", biz)) == [JAN, PIET, ANNA]
    first = call("GET", url, "owner1", biz, params={"limit": 2}).json()
    assert [c["phone"] for c in first["callers"]] == [JAN] and first["next"] == JAN
    rest = call("GET", url, "owner1", biz, params={"limit": 2, "before": first["next"]}).json()
    assert [c["phone"] for c in rest["callers"]] == [PIET, ANNA] and rest["next"] is None


def test_search_by_number_name_or_tag(biz, call):
    url = f"/v1/businesses/{biz}/callers"
    assert phones(call("GET", url, "owner1", biz, params={"q": "+31 61"})) == [JAN, PIET]
    assert phones(call("GET", url, "owner1", biz, params={"q": "0049151"})) == [ANNA]
    assert phones(call("GET", url, "owner1", biz, params={"q": "JAN"})) == [JAN]
    assert phones(call("GET", url, "owner1", biz, params={"tag": " VIP "})) == [JAN, ANNA]
    assert call("GET", url, "owner1", biz, params={"q": "jan", "tag": "vip"}).status_code == 422


def test_a_name_and_tags_are_cleaned_and_stamped(biz, call, services):
    url = f"/v1/businesses/{biz}/callers/%2B31613000000"
    body = {"name": "  Piet  Bakker ", "tags": ["Regular", "regular", " Late Night "]}
    saved = call("PATCH", url, "owner1", biz, json=body)
    assert saved.status_code == 200, saved.text
    assert (saved.json()["name"], saved.json()["tags"]) == ("Piet Bakker", ["regular", "late-night"])
    doc = callers(services, biz)[PIET]
    assert (doc["nameLower"], doc["updatedBy"], doc["lastCallAt"]) == ("piet bakker", "owner1", at(2))
    assert call("PATCH", url, "owner1", biz, json={"name": ""}).json()["tags"] == ["regular", "late-night"]
    for bad in ({}, {"name": "x" * 81}, {"tags": ["x" * 31]}, {"tags": [f"t{i}" for i in range(11)]},
                {"phone": "+1"}):
        assert call("PATCH", url, "owner1", biz, json=bad).status_code == 422, bad
    missing = f"/v1/businesses/{biz}/callers/%2B31600000000"
    assert call("PATCH", missing, "owner1", biz, json={"name": "x"}).status_code == 404
    local = f"/v1/businesses/{biz}/callers/0612"
    assert call("PATCH", local, "owner1", biz, json={"name": "x"}).status_code == 422


def test_scopes_decide_who_reads_and_who_changes(biz, call):
    url, one = f"/v1/businesses/{biz}/callers", f"/v1/businesses/{biz}/callers/%2B31612345678"
    assert call("GET", url, "temp", biz).status_code == 403
    assert call("GET", url, "clerk", biz).status_code == 200
    assert call("PATCH", one, "clerk", biz, json={"name": "x"}).status_code == 403
    assert call("GET", url, "seller").status_code == 200  # platform Businesses.View reads
    assert call("PATCH", one, "seller", json={"name": "x"}).status_code == 403
    assert call("PATCH", one, "boss", json={"name": "Jan"}).json()["updatedBy"] == "boss"
    assert call("GET", url, "owner1", "otherbiz").status_code == 404


def test_shapes_match_the_contract(biz, call):
    listed = call("GET", f"/v1/businesses/{biz}/callers", "owner1", biz).json()
    same_keys(listed, FIXTURE["callers"])
    url = f"/v1/businesses/{biz}/callers/%2B31612345678"
    same_keys(call("PATCH", url, "owner1", biz, json=FIXTURE["callerPatch"]).json(), FIXTURE["caller"])



def audits(services, biz: str) -> list[tuple[str, dict]]:
    rows = services.business_dbs[f"b-{biz}"].rows
    return [(r["action"], r["data"]) for (col, _), r in rows.items() if col == BusinessDb.AUDIT]


def test_block_and_unblock_an_existing_caller(biz, call, services):
    url = f"/v1/businesses/{biz}/callers/%2B31613000000"
    saved = call("PATCH", url, "owner1", biz, json={"blocked": True, "blockedReason": "  sells  energy "})
    assert saved.status_code == 200, saved.text
    row = saved.json()
    assert (row["blocked"], row["blockedReason"], row["blockedBy"]) == (True, "sells energy", "owner1")
    doc = callers(services, biz)[PIET]
    assert doc["blocked"] is True and doc["lastCallAt"] and "updatedBy" not in doc
    listed = call("GET", f"/v1/businesses/{biz}/callers", "owner1", biz, params={"blocked": "true"})
    assert [c["phone"] for c in listed.json()["callers"]] == [PIET]
    row = call("PATCH", url, "owner1", biz, json={"blocked": False}).json()
    gone = ("blockedReason", "blockedBy", "blockedAt")
    assert row["blocked"] is False and [row[k] for k in gone] == [None, None, None]
    assert not set(callers(services, biz)[PIET]) & set(gone)
    assert [a for a, _ in audits(services, biz)] == ["caller.block", "caller.unblock"]


def test_a_number_that_never_called_is_created_blocked(biz, call, services):
    url = f"/v1/businesses/{biz}/callers"
    made = call("POST", url, "owner1", biz, json=FIXTURE["callerBlock"])
    assert made.status_code == 200, made.text
    same_keys(made.json(), FIXTURE["blockedCaller"])
    doc = callers(services, biz)[NEW]
    assert set(doc) == {"phone", "blocked", "blockedReason", "blockedBy", "blockedAt"}
    assert call("POST", url, "owner1", biz, json={"phone": JAN}).json()["name"] == "Jan de Vries"
    for bad in ({"phone": "0612345678"}, {"phone": NEW, "blocked": False},
                {"phone": NEW, "blockedReason": "x" * 201}, {"phone": NEW, "name": "x"}):
        assert call("POST", url, "owner1", biz, json=bad).status_code == 422, bad
    assert call("GET", url, "owner1", biz, params={"blocked": "true", "tag": "vip"}).status_code == 422


def test_bad_patches_are_refused(biz, call):
    url = f"/v1/businesses/{biz}/callers/%2B31613000000"
    for bad in ({"blockedReason": "x"}, {"blocked": False, "blockedReason": "x"},
                {"blocked": True, "blockedReason": "x" * 201}):
        assert call("PATCH", url, "owner1", biz, json=bad).status_code == 422, bad
    missing = f"/v1/businesses/{biz}/callers/%2B31600000000"
    assert call("PATCH", missing, "owner1", biz, json={"blocked": True}).status_code == 404


def test_blocking_needs_its_own_scope(biz, call, services):
    bdb = services.business_dbs[f"b-{biz}"]
    bdb.rows[("roles", "editor")] = {"scopes": ["callers.edit"]}
    bdb.rows[("roles", "guard")] = {"scopes": ["callers.view", "callers.block"]}
    for uid in ("editor", "guard"):
        bdb.rows[("users", uid)] = {"role": uid}
    one, url = f"/v1/businesses/{biz}/callers/%2B31612345678", f"/v1/businesses/{biz}/callers"
    assert call("PATCH", one, "editor", biz, json={"blocked": True}).status_code == 403
    assert call("POST", url, "editor", biz, json={"phone": NEW}).status_code == 403
    assert call("PATCH", one, "editor", biz, json={"name": "Jan"}).status_code == 200
    assert call("PATCH", one, "guard", biz, json={"name": "Jan", "blocked": True}).status_code == 403
    assert call("PATCH", one, "guard", biz, json={"blocked": True}).status_code == 200
    assert call("PATCH", one, "seller", json={"blocked": False}).status_code == 403  # Businesses.View only
    assert call("POST", url, "boss", json={"phone": NEW}).json()["blockedBy"] == "boss"


def test_a_branch_limited_person_blocks_only_callers_of_their_branches(biz, call, services):
    bdb = services.business_dbs[f"b-{biz}"]
    bdb.rows[("roles", "guard")] = {"scopes": ["callers.view", "callers.block"]}
    bdb.rows[("users", "local")] = {"role": "guard", "branchIds": ["main"]}
    base = f"/v1/businesses/{biz}/callers"
    assert call("PATCH", f"{base}/%2B31612345678", "local", biz, json={"blocked": True}).status_code == 200
    assert call("PATCH", f"{base}/%2B4915112345678", "local", biz, json={"blocked": True}).status_code == 403
    assert call("POST", base, "local", biz, json={"phone": NEW}).status_code == 403
    assert ANNA not in [p for p, d in callers(services, biz).items() if d.get("blocked")]
