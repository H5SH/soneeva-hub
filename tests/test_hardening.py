"""Failures the hub must survive: lost keys, lost answers, retried events, forged issuers."""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest

from src.dependencies import _state
from src.models.admin import Scope
from src.models.auth import Audience
from src.models.platform import BusinessCreate
from src.services.tokens.replay import ReplayGuard
from src.services.tokens.verify import AuthError
from tests.setup.platform import business, checkout_event
from tests.test_plans import invoke

SECOND = BusinessCreate(ownerUid="owner2", name="B", type="shop", country="NL", timezone="UTC",
                        dataLocation="europe-west3")
NUMBERS = [Scope.HUB_NUMBER_VIEW, Scope.HUB_NUMBER_BUY, Scope.HUB_BUSINESS_VIEW]


def voice_token(private_pem: str, kid: str, sub: str) -> dict[str, str]:
    now = int(time.time())
    claims = {"iss": "soneeva-voice", "sub": sub, "aud": "karbon-hub", "jti": str(uuid.uuid4()),
              "iat": now, "exp": now + 60}
    token = jwt.encode(claims, private_pem, algorithm="EdDSA", headers={"kid": kid})
    return {"Authorization": f"Bearer {token}"}


def test_voice_keys_live_on_their_business_and_only_sign_for_it(client, bearer, services):
    invoke(services, "seed:soneeva")
    services.accounts.add("owner2", username="owner2")
    mine, other = business(services), asyncio.run(services.businesses.signups.create(SECOND, "t")).id
    kid, pem = asyncio.run(services.tools.issue_subject_key("soneeva-voice", mine))
    other_kid, _ = asyncio.run(services.tools.issue_subject_key("soneeva-voice", other))
    rows = services.db.rows
    assert rows[("businesses", mine)]["voiceKey"]["kid"] == kid and other_kid != kid
    assert not rows[("tools", "soneeva-voice")].get("keys")
    url = f"/v1/businesses/{mine}/plan"
    assert client.get(url, headers=voice_token(pem, kid, mine)).status_code == 200
    assert client.get(url, headers=voice_token(pem, kid, other)).status_code == 401  # another business's
    assert client.get(url, headers=voice_token(pem, other_kid, mine)).status_code == 401  # another key
    assert client.get(url, headers=voice_token(pem, kid, "a/b")).status_code == 401  # never picks a document
    shown = client.get(f"/v1/businesses/{mine}", headers=bearer("soneeva", NUMBERS, sub=mine)).json()
    assert "voiceKey" not in shown


def test_a_new_voice_key_replaces_the_old_at_once(client, services):
    invoke(services, "seed:soneeva")
    biz = business(services)
    kid, old = asyncio.run(services.tools.issue_subject_key("soneeva-voice", biz))
    url = f"/v1/businesses/{biz}/plan"
    assert client.get(url, headers=voice_token(old, kid, biz)).status_code == 200
    kid, new = asyncio.run(services.tools.issue_subject_key("soneeva-voice", biz))
    assert client.get(url, headers=voice_token(old, kid, biz)).status_code == 401
    assert client.get(url, headers=voice_token(new, kid, biz)).status_code == 200


def event(services, app, payload):
    hook = services.webhooks.live
    parsed = services.payment.live.parse_webhook(payload, None)
    return lambda after: hook.handle(app, parsed, after)


async def noop() -> None:
    return None


def test_one_apps_endpoint_never_uses_up_anothers_event(services):
    invoke(services, "seed:soneeva")
    soneeva = asyncio.run(services.admin.get_app("soneeva"))
    other = soneeva.model_copy(update={"id": "honeybook"})
    payload = json.dumps({"id": "evt_9", "type": "product.updated", "data": {"object": {
        "metadata": {"app": "soneeva"}}}}).encode()
    handle = event(services, other, payload)
    assert asyncio.run(handle(noop)) == "unmapped"
    handle = event(services, soneeva, payload)
    assert asyncio.run(handle(noop)) == "processed"
    assert asyncio.run(handle(noop)) == "duplicate"


def test_a_failed_voice_env_makes_stripe_retry_the_checkout(client, services):
    biz = business(services, subscribed=True)
    vault = services.voice._vault
    real, vault.write = vault.write, AsyncMock(side_effect=RuntimeError("Secret Manager is down"))
    with pytest.raises(RuntimeError):
        client.post("/webhooks/stripe/soneeva", content=checkout_event(biz))
    vault.write = real
    retried = client.post("/webhooks/stripe/soneeva", content=checkout_event(biz))
    assert retried.json()["result"] == "processed"
    assert f"voice-{biz}" in vault.versions


def test_provisioning_that_stopped_says_so(client, bearer, services):
    biz = business(services)
    row = services.db.rows[("businesses", biz)]
    row |= {"provisioned": False, "provisionError": None, "provisionStartedAt": None,
            "createdAt": "not a date"}
    headers = lambda: bearer("soneeva", NUMBERS, sub=biz)  # noqa: E731
    assert client.get(f"/v1/businesses/{biz}", headers=headers()).json()["provisionError"] is None
    row["provisionStartedAt"] = datetime.now(UTC) - timedelta(days=1)
    assert client.get(f"/v1/businesses/{biz}", headers=headers()).json()["provisionError"]


def test_updating_a_plans_amount_keeps_the_rest(client, bearer, services):
    invoke(services, "seed:soneeva")
    plans = services.plans.live
    saved = asyncio.run(plans.define("soneeva", "per-minute", amount=12))
    assert (saved.unit, saved.status, saved.amount) == ("minute", "active", 12)
    soon = asyncio.run(plans.define("soneeva", "base", amount=1100))
    assert soon.status == "soon"


def test_a_replay_is_refused_for_as_long_as_the_token_is_accepted():
    guard, now = ReplayGuard(), int(time.time())
    assert guard.check_and_record("jti", now + 30)
    assert not guard.check_and_record("jti", now + 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("issuer", ["a/b", "x", "UPPER"])
async def test_a_malformed_issuer_is_a_clean_refusal(verifier, sign, issuer):
    with pytest.raises(AuthError):
        await verifier.verify(sign(iss=issuer), Audience.API)


def test_a_lost_purchase_answer_doesnt_buy_the_number_twice(services):
    biz = business(services, subscribed=True)
    services.db.rows[("businesses", biz)]["voiceUrl"] = "https://voice.example"
    found = asyncio.run(services.businesses.get(biz))
    first = asyncio.run(services.numbers.buy(found, "+31201112222", "t"))
    found = asyncio.run(services.businesses.get(biz))
    again = asyncio.run(services.numbers.buy(found, "+31201112222", "t"))
    assert first.sid == again.sid and len(services.numbers._numbers.bought) == 1


def test_a_failed_secret_write_reuses_the_subaccount(services):
    biz = business(services, subscribed=True)
    services.db.rows[("businesses", biz)]["voiceUrl"] = "https://voice.example"
    found = asyncio.run(services.businesses.get(biz))
    voice = services.numbers._voice
    real, voice.add = voice.add, AsyncMock(side_effect=RuntimeError("Secret Manager is down"))
    with pytest.raises(RuntimeError):
        asyncio.run(services.numbers.buy(found, "+31201112222", "t"))
    sid = services.db.rows[("secrets", biz)]["twilioAccountSid"]
    voice.add = real
    asyncio.run(services.numbers.buy(found, "+31201112222", "t"))
    assert services.db.rows[("businesses", biz)]["twilioAccountSid"] == sid


def test_recreating_a_deleted_app_starts_clean(services):
    asyncio.run(services.admin.create_app("snapback", "Snapback", "", []))
    asyncio.run(services.admin.update_app("snapback", scopes=["hub.business.view"]))
    services.db.rows[("apps", "snapback")] |= {"stripeWebhookSecret": "whsec_old", "isDeleted": True}
    asyncio.run(services.admin.create_app("snapback", "Snapback", "", []))
    row = services.db.rows[("apps", "snapback")]
    assert row["scopes"] == [] and row["stripeWebhookSecret"] is None


def test_a_second_webhook_endpoint_replaces_the_first(services):
    asyncio.run(services.admin.create_app("snapback", "Snapback", "", []))
    url = "https://hub.example"
    first, _ = asyncio.run(services.admin.provision_webhook("snapback", url))
    asyncio.run(services.admin.provision_webhook("snapback", url))
    assert services.payment.live.deleted_endpoints == [first]


def test_a_sign_up_naming_an_unknown_app_is_refused(client, bearer):
    body = {"ownerUid": "owner1", "name": "B", "type": "shop", "country": "NL", "timezone": "UTC",
            "appId": "typo"}
    headers = bearer("soneeva", [Scope.HUB_BUSINESS_CREATE])
    assert client.post("/v1/businesses", json=body, headers=headers).status_code == 422


def test_a_dependency_on_a_service_that_doesnt_exist_stops_the_import():
    with pytest.raises(ValueError, match="nope"):
        _state("nope")
