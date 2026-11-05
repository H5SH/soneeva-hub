"""Internal tools: their own collection (the CLI and /recover manage them), never in payment listings."""

from src.models.admin import Scope
from tests.setup.platform import business
from tests.setup.staff import staff
from tests.test_contract_payment import check
from tests.test_plans import invoke


def test_tools_never_reach_payments(client, bearer, services):
    business(services)
    services.db.rows[("tools", "soneeva-voice")] = {"name": "Voice", "keys": []}
    listed = client.get("/v1/payment/apps", headers=staff(services, bearer)()).json()["apps"]
    assert [a["id"] for a in listed] == ["soneeva"]
    assert client.get("/v1/public/plans?app=karbon-admin").json() == {"plans": []}
    assert client.post("/webhooks/stripe/soneeva-voice", content=b"{}").status_code == 404


def test_seed_makes_the_tools_and_keeps_their_keys(services):
    invoke(services, "seed:soneeva")
    key = {"kid": "k1", "publicKey": "pem", "status": "active"}
    services.db.rows[("tools", "karbon-admin")]["keys"] = [key]
    invoke(services, "seed:soneeva")
    assert services.db.rows[("tools", "karbon-admin")]["keys"] == [key]
    assert not services.db.rows[("tools", "karbon-admin")]["subjectKeys"]
    assert "hub.people.update" in services.db.rows[("tools", "karbon-admin")]["scopes"]


def test_the_hub_name_is_never_an_app(client, bearer, services):
    made = client.post("/v1/payment/apps", json={"id": "karbon-hub"}, headers=staff(services, bearer)())
    assert made.status_code == 400


def test_a_voice_key_is_per_business_and_never_rotated_by_hand(client, bearer, services):
    biz = business(services)
    url = f"/v1/businesses/{biz}/plan"
    status = client.get(url, headers=bearer("soneeva-voice", [Scope.HUB_PLAN_VIEW], sub=biz))
    check("planStatus", status.json())
    assert status.json()["active"] is False and status.json()["test"] is False
    other = client.get(url, headers=bearer("soneeva-voice", [Scope.HUB_PLAN_VIEW], sub="zzz"))
    assert other.status_code == 403
