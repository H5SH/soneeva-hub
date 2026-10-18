"""Where a business's data goes: nested in `(default)`, nested in a region's database, or its own."""

import asyncio
from datetime import UTC, datetime

from src.db import NESTED_INDEXES, FakeDatabase, Query, business_db, place
from src.models.db import Sub
from src.services.platform.calls import CallService
from tests.test_businesses import CONSOLE, SIGNUP

US = SIGNUP | {"country": "US", "timezone": "America/New_York"}
NEARBY = {k: v for k, v in SIGNUP.items() if k != "dataLocation"}  # NL: the EU by country


def sign_up(client, bearer, body):
    return client.post("/v1/businesses", json=body, headers=bearer("soneeva", CONSOLE))


def test_the_dbid_says_where_the_data_is():
    assert place("abc", "") == place("abc", None) == type(place("abc", ""))(None, "businesses/abc")
    assert place("abc", "eu/abc").database == "region-eu" and place("abc", "eu/abc").root == "businesses/abc"
    assert place("abc", "b-abc").database == "b-abc" and place("abc", "b-abc").root == ""


def test_the_default_region_nests_the_business_in_the_default_database(client, bearer, services):
    us = {k: v for k, v in US.items() if k != "dataLocation"}
    made = sign_up(client, bearer, us).json()
    assert made["dbId"] == "" and made["dataLocation"] == ""
    assert services.db.rows[(f"businesses/{made['id']}/branches", "main")]["phone"] == "+31201234567"
    assert services.db.indexed["(default)"] == set(NESTED_INDEXES) and not services.db.databases
    business = asyncio.run(services.businesses.get(made["id"]))
    assert [b["id"] for b in asyncio.run(services.businesses.branches(business))] == ["main"]


def test_a_country_picks_its_region_and_its_shared_database(client, bearer, services):
    made = sign_up(client, bearer, NEARBY).json()
    assert made["dbId"] == f"eu/{made['id']}"
    assert services.db.placed == {"region-eu": "eur3"}
    shared = services.business_dbs["region-eu"]
    assert (f"businesses/{made['id']}/branches", "main") in shared.rows
    assert services.db.indexed["region-eu"] == set(NESTED_INDEXES)


def test_a_region_or_a_custom_location_is_chosen_and_checked(client, bearer, services):
    assert sign_up(client, bearer, NEARBY | {"dataRegion": "mars"}).status_code == 422
    assert sign_up(client, bearer, NEARBY | {"dataLocation": "moon-1"}).status_code == 422
    assert sign_up(client, bearer, NEARBY | {"dataRegion": "us"}).json()["dbId"] == ""


def test_sign_up_lists_the_regions_and_the_locations(client, bearer):
    offered = client.get("/v1/data-locations", headers=bearer("soneeva", CONSOLE)).json()
    assert offered["default"] == "us"
    assert [r["id"] for r in offered["regions"]] == ["us", "eu"]
    assert "NL" in offered["regions"][1]["countries"]
    assert {"id": "europe-west3", "name": "Frankfurt"} in offered["locations"]


def test_a_nested_business_sees_only_its_own_subtree():
    db = FakeDatabase()
    mine, theirs = business_db(db, "mine", ""), business_db(db, "theirs", "")
    calls = lambda who: f"callers/+31/{Sub.CALLS}"  # noqa: E731
    asyncio.run(mine.set(calls("mine"), "c1", {"businessId": "mine", "startedAt": 1}))
    asyncio.run(theirs.set(calls("theirs"), "c2", {"businessId": "theirs", "startedAt": 2}))
    assert asyncio.run(mine.get(calls("mine"), "c2")) is None
    group = Query(Sub.CALLS, group=True).order_by("startedAt")
    assert [d.id for d in asyncio.run(mine.query(group))] == ["c1"]
    assert asyncio.run(theirs.count(Query(Sub.CALLS, group=True))) == 1
    assert ("businesses/mine/callers/+31/calls", "c1") in db.rows


def test_the_demo_totals_leave_nested_businesses_calls_out():
    db = FakeDatabase({
        ("callers/demo1/calls", "d1"): {"caller_id": "demo1", "started_at": datetime.now(UTC),
                                        "message_count": 2},
        ("businesses/b1/callers/+31/calls", "c1"): {"businessId": "b1", "startedAt": datetime.now(UTC)},
    })
    totals = asyncio.run(CallService(db).totals(None))
    assert totals["calls"] == 1 and totals["messages"] == 2
