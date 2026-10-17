"""Numbers only on a plan; a paid checkout deploys the voice service."""

import asyncio

import pytest

from src.models.admin import Scope
from tests.setup.platform import business, checkout_event, subscribe

NUMBERS = [Scope.HUB_NUMBER_VIEW, Scope.HUB_NUMBER_BUY, Scope.HUB_VOICE_UPDATE]


def test_numbers_are_refused_without_a_subscription(client, bearer, services):
    biz = business(services)
    headers = bearer("soneeva", NUMBERS, sub=biz)
    assert client.get(f"/v1/businesses/{biz}/numbers?country=NL", headers=headers).status_code == 402
    buy = client.post(f"/v1/businesses/{biz}/numbers", json={"phoneNumber": "+31201112222"},
                      headers=bearer("soneeva", NUMBERS, sub=biz))
    assert buy.status_code == 402


def test_buying_creates_one_subaccount_and_points_at_the_voice(client, bearer, services):
    biz = business(services, subscribed=True)
    services.db.rows[("businesses", biz)]["voiceUrl"] = "https://voice.example"
    found = client.get(f"/v1/businesses/{biz}/numbers?country=NL&contains=55",
                       headers=bearer("soneeva", NUMBERS, sub=biz)).json()["numbers"]
    for phone in (found[0]["phoneNumber"], found[1]["phoneNumber"]):
        assert client.post(f"/v1/businesses/{biz}/numbers", json={"phoneNumber": phone},
                           headers=bearer("soneeva", NUMBERS, sub=biz)).status_code == 201
    fake = services.numbers._numbers
    assert len(fake.subaccounts) == 1
    assert fake.bought[0][2] == "https://voice.example/voice"
    assert len(services.db.rows[("businesses", biz)]["numbers"]) == 2
    assert services.db.rows[("secrets", biz)]["twilioAuthToken"] == "fake-token"
    assert len(services.voice._deployer.deployed) == 2  # each new number restarts with its call menu
    assert services.business_dbs[f"b-{biz}"].rows[("numbers", found[0]["phoneNumber"])]["direct"] is True


def test_checkout_completed_deploys_voice_with_the_contract_env(client, services):
    biz = business(services)
    subscribe(services, biz)
    response = client.post("/webhooks/stripe/soneeva", content=checkout_event(biz))
    assert response.json() == {"result": "processed"}
    (business_id, env), = services.voice._deployer.deployed
    assert business_id == biz
    assert env["FIRESTORE_DATABASE"] == f"b-{biz}" and env["HUB_APP_ID"] == "soneeva-voice"
    assert env["HUB_KEY_ID"] == f"sub-{biz}" and env["CALL_LANGUAGE"] == "auto"
    row = services.db.rows[("businesses", biz)]
    assert row["voiceUrl"] == f"https://voice-{biz}.example" and "plan" not in row
    again = client.post("/webhooks/stripe/soneeva", content=checkout_event(biz))
    assert again.json() == {"result": "duplicate"} and len(services.voice._deployer.deployed) == 1


def test_voice_settings_update_the_agent_and_restart(client, bearer, services):
    biz = business(services)
    services.db.rows[("businesses", biz)]["voiceUrl"] = "https://voice.example"
    response = client.post(f"/v1/businesses/{biz}/voice/settings", json={"language": "nl", "voice": "x"},
                           headers=bearer("soneeva", NUMBERS, sub=biz))
    assert response.json()["restarting"] is True
    assert services.business_dbs[f"b-{biz}"].rows[("settings", "agent")]["language"] == "nl"
    assert services.voice._deployer.deployed[-1][1]["CALL_LANGUAGE"] == "nl"


async def test_local_driver_refuses_a_folder_that_is_not_soneeva_voice(tmp_path):
    from src.deploy import DeployError, LocalDeployer

    driver = LocalDeployer(str(tmp_path), 8100, home=tmp_path / "state")
    with pytest.raises(DeployError, match="not a soneeva-voice checkout"):
        await driver.deploy("biz", {})


def test_the_agent_connection_comes_from_the_business_and_redeploys_its_voice(client, bearer, services):
    biz = business(services)
    services.db.rows[("users", "boss")] = {"uid": "boss", "role": "superadmin"}
    as_boss = lambda: bearer("karbon-admin", [Scope.HUB_BUSINESS_UPDATE], uid="boss")  # noqa: E731
    url = f"/v1/businesses/{biz}"
    body = {"mcpUrl": "https://mcp.example/mcp", "whatsappFrom": "whatsapp:+31201112222"}
    saved = client.patch(url, json=body, headers=as_boss()).json()
    assert saved["mcpUrl"] == body["mcpUrl"] and saved["redeploying"] is False  # no voice yet: only saved
    assert services.voice._deployer.deployed == []
    services.db.rows[("businesses", biz)]["voiceUrl"] = "https://voice.example"
    assert client.patch(url, json=body, headers=as_boss()).json()["redeploying"] is False  # unchanged
    assert client.patch(url, json={"whatsappFrom": ""}, headers=as_boss()).json()["redeploying"] is True
    (deployed_biz, env), = services.voice._deployer.deployed
    assert deployed_biz == biz and env["MCP_URL"] == body["mcpUrl"] and "WHATSAPP_FROM" not in env
    for bad in ({"mcpUrl": "http://mcp.example"}, {"whatsappFrom": "+31201112222"}, {"mcpUrl": None}):
        assert client.patch(url, json=bad, headers=as_boss()).status_code == 422
    owner = bearer("soneeva", [Scope.HUB_BUSINESS_UPDATE], sub=biz)
    assert client.patch(url, json={"mcpUrl": ""}, headers=owner).status_code == 403


def test_voice_never_gets_the_master_twilio_pair(services, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_master")
    monkeypatch.setenv("MCP_URL", "https://ignored.example")
    services.voice._project = "proj"
    biz = business(services)
    env = asyncio.run(services.voice.env(asyncio.run(services.businesses.require(None, biz))))
    assert "TWILIO_ACCOUNT_SID" not in env and "MCP_URL" not in env and "WHATSAPP_FROM" not in env
    assert env["STORAGE_BUCKET"] == "gs://proj.firebasestorage.app"
