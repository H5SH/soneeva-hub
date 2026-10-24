"""/v1/platform/calls and sign-up creating the owner's account."""

from datetime import UTC, datetime

import pytest

from src.models.db import Default
from tests.setup.people import APP, as_, people
from tests.test_plans import invoke

OWNER = {"email": "rosa@example.com", "password": "secret1", "displayName": "Rosa", "username": "rosa"}
SIGNUP = {
    "owner": OWNER,
    "name": "Pizza Roma",
    "type": "restaurant",
    "country": "NL",
    "timezone": "Europe/Amsterdam",
}


@pytest.fixture
def platform(services):
    people(services)
    for n, caller in enumerate(["+31600000001", "+31600000002", "+31600000000"]):
        services.db.rows[(f"callers/{caller}/calls", f"c{n}")] = {
            "caller_id": caller,
            "started_at": datetime(2026, 10, n + 1, tzinfo=UTC),
            "message_count": 2,
            "cost": {"total": 0.5},
        }
    for message in ({"index": 0, "role": "user", "content": "hi"},
                    {"index": 1, "role": "assistant", "content": "hello"}):
        services.db.rows[("callers/+31600000001/calls/c0/messages", str(message["index"]))] = message
    return services


CALL = "/v1/platform/calls/+31600000001"


def test_calls_follow_the_scope_reach(platform, client, bearer):
    everyone = client.get("/v1/platform/calls?scope=Chats.View&limit=2", headers=as_(bearer, "admin")).json()
    assert [c["id"] for c in everyone["calls"]] == ["c2", "c1"] and everyone["next"]
    rest = client.get(
        f"/v1/platform/calls?scope=Chats.View&before={everyone['next']}", headers=as_(bearer, "admin")
    )
    assert [c["id"] for c in rest.json()["calls"]] == ["c0"]
    mine = client.get("/v1/platform/calls?scope=Chats.View", headers=as_(bearer, "sp")).json()
    assert mine["calls"] == []  # callers are phone numbers: only an unrestricted reach sees them
    assert client.get("/v1/platform/calls?scope=Roles.Write", headers=as_(bearer, "admin")).status_code == 422
    assert client.get("/v1/platform/calls?scope=Chats.View", headers=as_(bearer, "u1")).status_code == 403


def test_one_call_its_messages_and_totals(platform, client, bearer):
    call = client.get(f"{CALL}/c0?scope=Chats.View", headers=as_(bearer, "admin")).json()
    assert call["startedAt"] == "2026-10-01T00:00:00.000Z" and call["cost"]["total"] == 0.5
    later = client.get(
        "/v1/platform/calls/+31600000001/c0/messages?scope=Chats.View&after=0", headers=as_(bearer, "admin")
    )
    assert [m["content"] for m in later.json()["messages"]] == ["hello"]
    assert (
        client.get("/v1/platform/calls/+31600000002/c1?scope=Chats.View", headers=as_(bearer, "sp"))
        .status_code
        == 404
    )
    totals = client.get("/v1/platform/calls/totals?scope=Chats.View", headers=as_(bearer, "admin")).json()
    assert totals == {"calls": 3, "messages": 6, "cost": 1.5}
    some = client.get(
        "/v1/platform/calls/totals?scope=Chats.View&callers=%2B31600000001,%2B31600000002",
        headers=as_(bearer, "sp"),
    )
    assert some.json()["calls"] == 0


def test_the_scope_catalogue(platform, client, bearer, services):
    services.db.rows[(Default.SCOPES.value, "soneeva")] = {
        "scopes": [{"id": "calls.view", "group": "calls"}]
    }
    assert client.get("/v1/scopes/soneeva", headers=bearer("bare", [], sub="biz1")).status_code == 403
    scopes = client.get("/v1/scopes/soneeva", headers=as_(bearer, "u1", sub="-")).json()["scopes"]
    assert scopes == [{"id": "calls.view", "group": "calls"}]


def test_signup_creates_the_owner_and_rolls_back_on_failure(platform, client, bearer, services):
    invoke(services, "seed:soneeva")
    made = client.post("/v1/businesses", json=SIGNUP, headers=bearer("console", APP))
    assert made.status_code == 201, made.text
    owner = made.json()["ownerUid"]
    assert services.accounts.claims[owner] == {"username": "rosa", "businessId": made.json()["id"]}
    again = client.post("/v1/businesses", json=SIGNUP, headers=bearer("console", APP))
    assert again.status_code == 409

    def broken(collection, row_id, data):
        raise RuntimeError("firestore down")

    services.db.set_row = broken  # the business row can't be written: the new account goes again
    with pytest.raises(RuntimeError):
        client.post(
            "/v1/businesses",
            json=SIGNUP | {"owner": OWNER | {"email": "x@example.com"}},
            headers=bearer("console", APP),
        )
    assert all(a.email != "x@example.com" for a in services.accounts.accounts.values())


def test_calls_per_day_split_the_cost_and_fill_gaps(platform, client, bearer):
    platform.db.rows[("callers/+31600000001/calls", "c0")] |= {
        "duration_seconds": 90, "cost": {"llm": 0.1, "stt": 0.1, "tts": 0.2, "carrier": 0.1, "total": 0.5}}
    url = "/v1/platform/calls/daily?scope=Chats.View&from=2026-09-30T00:00:00Z&to=2026-10-03T23:59:59Z"
    days = client.get(url, headers=as_(bearer, "admin")).json()["days"]
    assert [d["day"] for d in days] == ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"]
    assert days[0]["calls"] == 0 and days[2]["calls"] == 1
    assert days[1] == {"day": "2026-10-01", "calls": 1, "minutes": 1.5, "llm": 0.1, "stt": 0.1, "tts": 0.2,
                       "live": 0.0, "telephony": 0.1, "total": 0.5}
    mine = client.get(url, headers=as_(bearer, "sp")).json()["days"]
    assert sum(d["calls"] for d in mine) == 0  # an assigned reach sees no phone callers
    call = client.get(f"{CALL}/c0?scope=Chats.View", headers=as_(bearer, "admin")).json()
    assert call["cost"]["telephony"] == 0.1 and call["engine"] == "smart"  # before Spark: Smart


def test_spark_calls_cost_live_and_filter_by_engine(platform, client, bearer):
    platform.db.rows[("callers/+31600000001/calls", "c0")] |= {
        "engine": "spark", "duration_seconds": 60, "cost": {"live": 0.02, "carrier": 0.01, "total": 0.03}}
    url = "/v1/platform/calls/daily?scope=Chats.View&from=2026-09-30T00:00:00Z&to=2026-10-03T23:59:59Z"
    spark = client.get(url + "&engine=spark", headers=as_(bearer, "admin")).json()["days"]
    assert sum(d["calls"] for d in spark) == 1 and spark[1]["live"] == 0.02 and spark[1]["stt"] == 0
    smart = client.get(url + "&engine=smart", headers=as_(bearer, "admin")).json()["days"]
    assert all(d["live"] == 0 for d in smart) and spark[1]["calls"] + smart[1]["calls"] == 1
    calls = client.get("/v1/platform/calls?scope=Chats.View&engine=spark", headers=as_(bearer, "admin"))
    assert [c["engine"] for c in calls.json()["calls"]] == ["spark"]
    assert client.get(url + "&engine=fast", headers=as_(bearer, "admin")).status_code == 422
