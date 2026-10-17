"""The tightened verification rules for callers' tokens."""

import time
import uuid

import jwt
import pytest

from src.models.admin import Scope
from src.models.auth import Audience
from src.services.tokens.verify import AuthError


async def test_token_living_longer_than_60_seconds_is_rejected(verifier, sign):
    with pytest.raises(AuthError, match="lifetime"):
        await verifier.verify(sign(exp_delta=3600), Audience.API)


async def test_subject_key_only_signs_for_its_business(services, bearer, client):
    await services.tools.create("soneeva-voice", "Voice", [str(Scope.HUB_USAGE_WRITE)], subject_keys=True)
    kid, private_pem = await services.tools.issue_subject_key("soneeva-voice", "biz1")
    now = int(time.time())
    token = jwt.encode({"iss": "soneeva-voice", "sub": "other", "aud": "karbon-hub",
                        "jti": str(uuid.uuid4()), "iat": now, "exp": now + 60},
                       private_pem, algorithm="EdDSA", headers={"kid": kid})
    response = client.post("/v1/usage", json={"callId": "c1", "seconds": 5},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "subject" in response.json()["detail"]


def test_missing_bearer_is_401(client):
    assert client.post("/v1/usage", json={"callId": "c", "seconds": 1}).status_code == 401


def test_app_without_the_scope_is_403(client, bearer):
    headers = bearer("console", [Scope.HUB_BUSINESS_VIEW], sub="biz1")
    assert client.post("/v1/usage", json={"callId": "c", "seconds": 1}, headers=headers).status_code == 403
