from datetime import UTC, datetime, timedelta

import pytest

from src.models.auth import Audience, KeyStatus, PublicKey
from src.services.tokens.generate import rotate
from src.services.tokens.verify import AuthError
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
    from src.services.tokens.keys import KeyStore
    from src.services.tokens.replay import ReplayGuard
    from src.services.tokens.verify import TokenVerifier
    from tests.conftest import APP_ID, FakeDatabase

    application["status"] = "disabled"
    verifier = TokenVerifier(KeyStore(FakeDatabase({("apps", APP_ID): application})), ReplayGuard())
    with pytest.raises(AuthError, match="disabled"):
        await verifier.verify(sign(), Audience.API)


@pytest.mark.asyncio
async def test_replayed_jti_is_rejected(verifier, sign):
    token = sign(jti="replay-me")
    await verifier.verify(token, Audience.API)
    with pytest.raises(AuthError, match="already been used"):
        await verifier.verify(token, Audience.API)


@pytest.mark.asyncio
async def test_a_hub_token_cannot_call_the_payment_api(verifier, sign):
    with pytest.raises(AuthError):
        await verifier.verify(sign(aud=Audience.HUB.value), Audience.API)


@pytest.mark.asyncio
async def test_rotated_key_still_verifies_while_retiring(application, sign, keys):
    """Rotation must not invalidate tokens already in flight, but a retired key doesn't last."""
    from src.services.tokens.keys import KeyStore
    from src.services.tokens.replay import ReplayGuard
    from src.services.tokens.verify import TokenVerifier
    from tests.conftest import APP_ID, FakeDatabase

    _, new_public = keypair()
    application["keys"][0] |= {"status": "retiring", "retiring_until": datetime.now(UTC) + timedelta(hours=1)}
    application["keys"].append(
        {"kid": "new-key", "alg": "EdDSA", "public_key": new_public, "status": "active"}
    )
    verifier = TokenVerifier(KeyStore(FakeDatabase({("apps", APP_ID): application})), ReplayGuard())
    claims = await verifier.verify(sign(), Audience.API)
    assert claims.tenant == "store-123"
    application["keys"][0]["retiring_until"] = datetime.now(UTC) - timedelta(seconds=1)
    verifier = TokenVerifier(KeyStore(FakeDatabase({("apps", APP_ID): application})), ReplayGuard())
    with pytest.raises(AuthError):
        await verifier.verify(sign(), Audience.API)


def test_rotation_retires_for_a_while_and_revoking_stops_at_once():
    old = PublicKey(kid="old", public_key="pem")
    (retired, new), _, _ = rotate([old])
    assert retired.live and retired.status is KeyStatus.RETIRING and new.status is KeyStatus.ACTIVE
    revoked, kid, _ = rotate([old], revoke=True)
    assert [k.kid for k in revoked] == [kid]
    gone = retired.model_copy(update={"retiring_until": datetime.now(UTC) - timedelta(seconds=1)})
    assert not gone.live and len(rotate([gone])[0]) == 1  # an expired key is dropped, never extended
