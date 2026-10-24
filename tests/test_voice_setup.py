"""Numbers only on a plan; a paid checkout puts the voice env in Secret Manager; the developer deploys
the voice by hand and its URL (Voice.Url.Write) points the numbers at it; a sandbox twin for testing."""

import asyncio
import json
from pathlib import Path

from src.models.admin import Scope
from src.services.voice import from_dotenv
from tests.setup.platform import business, checkout_event, subscribe

FIXTURE = json.loads((Path(__file__).parent / "contract" / "hub.json").read_text())

UPDATE = [Scope.HUB_BUSINESS_UPDATE, Scope.HUB_BUSINESS_VIEW]
#: An app's scopes are registered once per test, so the console always holds all of these.
NUMBERS = [Scope.HUB_NUMBER_VIEW, Scope.HUB_NUMBER_BUY, Scope.HUB_VOICE_UPDATE, *UPDATE]


def secret(services, biz) -> dict[str, str]:
    return from_dotenv(services.voice._vault.versions[f"voice-{biz}"][-1])


def person(services, bearer, uid, role):
    """Fresh headers per call: each token is used once."""
    services.db.rows[("users", uid)] = {"uid": uid, "role": role}
    return lambda: bearer("karbon-admin", UPDATE, uid=uid)


def test_numbers_are_refused_without_a_subscription(client, bearer, services):
    biz = business(services)
    headers = lambda: bearer("soneeva", NUMBERS, sub=biz)  # noqa: E731
    assert client.get(f"/v1/businesses/{biz}/numbers?country=NL", headers=headers()).status_code == 402
    buy = client.post(f"/v1/businesses/{biz}/numbers", json={"phoneNumber": "+31201112222"},
                      headers=headers())
    assert buy.status_code == 402


def test_a_checkout_writes_the_voice_env_once_and_deploys_nothing(client, services):
    biz = business(services)
    subscribe(services, biz)
    paid = client.post("/webhooks/stripe/soneeva", content=checkout_event(biz))
    assert paid.json() == {"result": "processed"}
    env = secret(services, biz)
    assert env["FIRESTORE_DATABASE"] == f"b-{biz}" and env["HUB_APP_ID"] == "soneeva-voice"
    assert env["HUB_KEY_ID"] == f"sub-{biz}" and env["HUB_PRIVATE_KEY"].startswith("-----BEGIN")
    assert "\n" in env["HUB_PRIVATE_KEY"]  # a real PEM again once read back
    assert not {"TWILIO_ACCOUNT_SID", "MCP_URL", "CALL_LANGUAGE", "VOICE"} & set(env)
    assert services.db.rows[("businesses", biz)].get("voiceUrl") is None
    asyncio.run(services.voice.provide(biz))  # a repeat leaves the secret (and its key) alone
    assert len(services.voice._vault.versions[f"voice-{biz}"]) == 1


def test_the_voice_url_needs_its_scope_and_points_every_number(client, bearer, services):
    biz = business(services, subscribed=True)
    url = f"/v1/businesses/{biz}"
    sales = person(services, bearer, "sam", "sales")
    assert client.patch(url, json={"voiceUrl": "https://voice.example"}, headers=sales()).status_code == 403
    owner = bearer("soneeva", NUMBERS, sub=biz)
    assert client.patch(url, json={"voiceUrl": "https://voice.example"}, headers=owner).status_code == 403
    dev = person(services, bearer, "dev", "developer")
    assert client.patch(url, json={"voiceUrl": "http://voice.example"}, headers=dev()).status_code == 422
    saved = client.patch(url, json={"voiceUrl": "https://voice.example/"}, headers=dev())
    assert saved.status_code == 200 and saved.json()["voiceUrl"] == "https://voice.example"

    buying = lambda: bearer("soneeva", NUMBERS, sub=biz)  # noqa: E731
    listed = client.get(f"{url}/numbers?country=NL&contains=55", headers=buying())
    assert listed.status_code == 200, listed.text
    found = listed.json()["numbers"]
    for phone in (found[0]["phoneNumber"], found[1]["phoneNumber"]):
        assert client.post(url + "/numbers", json={"phoneNumber": phone}, headers=buying()).status_code == 201
    fake = services.numbers._numbers
    assert len(fake.subaccounts) == 1 and fake.bought[0][2] == "https://voice.example/voice"
    assert secret(services, biz)["TWILIO_AUTH_TOKEN"] == "fake-token"  # the next revision gets it
    assert services.business_dbs[f"b-{biz}"].rows[("numbers", found[0]["phoneNumber"])]["direct"] is True

    assert client.patch(url, json={"voiceUrl": "https://voice-2.example"}, headers=dev()).status_code == 200
    assert set(fake.pointed.values()) == {"https://voice-2.example/voice"} and len(fake.pointed) == 2


def test_the_deployment_step_is_ticked_by_staff(client, bearer, services):
    biz = business(services)
    boss = person(services, bearer, "boss", "superadmin")
    done = client.post(f"/v1/businesses/{biz}/checklist/deployment", json={"done": True}, headers=boss())
    assert done.json() == {"id": "deployment", "state": "done"}
    menu = client.post(f"/v1/businesses/{biz}/checklist/menu", json={"done": True}, headers=boss())
    assert menu.status_code == 404  # not a staff item
    owner = bearer("soneeva", NUMBERS, sub=biz)
    refused = client.post(f"/v1/businesses/{biz}/checklist/deployment", json={"done": True}, headers=owner)
    assert refused.status_code == 403


def test_a_sandbox_twin_is_its_own_unlisted_test_business_with_a_rotating_key(client, bearer, services):
    biz = business(services)
    sales = person(services, bearer, "sam", "sales")
    assert client.post(f"/v1/businesses/{biz}/sandbox/key", headers=sales()).status_code == 403
    dev = person(services, bearer, "dev", "developer")
    first = client.post(f"/v1/businesses/{biz}/sandbox/key", headers=dev()).json()
    assert set(first) == set(FIXTURE["sandboxKey"])
    twin = first["businessId"]
    env = from_dotenv(first["env"])
    assert env["BUSINESS_ID"] == twin != biz and env["FIRESTORE_DATABASE"] == ""
    assert "TWILIO_ACCOUNT_SID" not in env
    row = services.db.rows[("businesses", twin)]
    assert row["sandboxOf"] == biz and row["test"] and row["provisioned"]
    assert services.db.rows[("businesses", biz)]["sandboxId"] == twin
    assert services.accounts.claims["owner1"]["businessId"] == biz  # the owner stays on the live one
    assert (f"businesses/{twin}/branches", "main") in services.db.rows
    assert twin not in [b["id"] for b in client.get("/v1/businesses", headers=dev()).json()["businesses"]]
    assert client.post(f"/v1/businesses/{biz}/sandbox", headers=dev()).json()["id"] == twin
    again = client.post(f"/v1/businesses/{biz}/sandbox/key", headers=dev()).json()
    assert again["businessId"] == twin and again["env"] != first["env"]
    tool = services.db.rows[("tools", "soneeva-voice")]["keys"]
    assert len([k for k in tool if k.get("subject") == twin]) == 1  # the previous key is gone
    voice = bearer("soneeva-voice", [Scope.HUB_PLAN_VIEW], sub=twin)
    plan = client.get(f"/v1/businesses/{twin}/plan", headers=voice)
    assert plan.json()["active"] is True and plan.json()["status"] == "sandbox"
