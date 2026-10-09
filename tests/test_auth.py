import pytest

from app.models.auth import Audience
from app.services.tokens.verify import AuthError
from tests.conftest import keypair


@pytest.mark.asyncio
async def test_valid_token_is_accepted(verifier, sign):
    claims = await verifier.verify(sign(), Audience.API)
    assert claims.iss == "softbox"
    assert claims.tenant == "store-123"


@pytest.mark.asyncio
async def test_token_signed_by_another_key_is_rejected(verifier, sign):
    other_private, _ = keypair()
    with pytest.raises(AuthError):
        await verifier.verify(sign(key=other_private), Audience.API)


@pytest.mark.asyncio
async def test_expired_token_is_rejected(verifier, sign):
    # Beyond the 30s leeway.
    with pytest.raises(AuthError):
        await verifier.verify(sign(exp_delta=-120), Audience.API)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "jti", "aud"])
async def test_missing_required_claim_is_rejected(verifier, sign, claim):
    """A token with no exp used to verify forever — `require` is what closes that."""
    with pytest.raises(AuthError):
        await verifier.verify(sign(omit=(claim,)), Audience.API)


@pytest.mark.asyncio
async def test_hs256_token_is_rejected(verifier):
    """Algorithm confusion: an attacker signing HS256 with the public key as the secret."""
    import time

    import jwt

    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": "softbox",
            "sub": "store-123",
            "aud": Audience.API.value,
            "jti": "x",
            "iat": now,
            "exp": now + 60,
        },
        "not-the-key",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    with pytest.raises(AuthError):
        await verifier.verify(forged, Audience.API)


@pytest.mark.asyncio
async def test_unknown_issuer_is_rejected(verifier, sign):
    with pytest.raises(AuthError, match="Unknown issuer"):
        await verifier.verify(sign(iss="somebody-else"), Audience.API)


@pytest.mark.asyncio
async def test_unknown_kid_is_rejected(verifier, sign):
    with pytest.raises(AuthError, match="Unknown signing key"):
        await verifier.verify(sign(kid="rotated-away"), Audience.API)


@pytest.mark.asyncio
async def test_disabled_application_is_rejected(application, sign):
    from app.services.tokens.keys import KeyStore
    from app.services.tokens.replay import ReplayGuard
    from app.services.tokens.verify import TokenVerifier
    from tests.conftest import APP_ID, FakeDatabase

    application["status"] = "disabled"
    verifier = TokenVerifier(KeyStore(FakeDatabase({APP_ID: application})), ReplayGuard())
    with pytest.raises(AuthError, match="disabled"):
        await verifier.verify(sign(), Audience.API)


@pytest.mark.asyncio
async def test_replayed_jti_is_rejected(verifier, sign):
    token = sign(jti="replay-me")
    await verifier.verify(token, Audience.API)
    with pytest.raises(AuthError, match="already been used"):
        await verifier.verify(token, Audience.API)


@pytest.mark.asyncio
async def test_hosted_token_cannot_call_the_json_api(verifier, sign):
    """A leaked ?token= from a referer header must not reach the JSON API."""
    with pytest.raises(AuthError):
        await verifier.verify(sign(aud=Audience.HOSTED.value), Audience.API)


@pytest.mark.asyncio
async def test_api_token_cannot_be_used_as_a_url_token(verifier, sign):
    with pytest.raises(AuthError):
        await verifier.verify(sign(aud=Audience.API.value), Audience.HOSTED)


@pytest.mark.asyncio
async def test_rotated_key_still_verifies_while_retiring(application, sign, keys):
    """Rotation must not invalidate tokens already in flight."""
    from app.services.tokens.keys import KeyStore
    from app.services.tokens.replay import ReplayGuard
    from app.services.tokens.verify import TokenVerifier
    from tests.conftest import APP_ID, FakeDatabase

    _, new_public = keypair()
    application["keys"][0]["status"] = "retiring"
    application["keys"].append(
        {"kid": "new-key", "alg": "EdDSA", "public_key": new_public, "status": "active"}
    )
    verifier = TokenVerifier(KeyStore(FakeDatabase({APP_ID: application})), ReplayGuard())
    claims = await verifier.verify(sign(), Audience.API)
    assert claims.tenant == "store-123"
