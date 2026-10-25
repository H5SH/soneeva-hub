import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.auth import FakeAccounts
from src.db import FakeDatabase
from src.models.auth import Audience
from src.numbers import FakeNumbers
from src.payment import FakePayment
from src.reader import FakeReader
from src.remote import FakeRemote
from src.services.modes import ByMode
from src.services.tokens.keys import KeyStore
from src.services.tokens.replay import ReplayGuard
from src.services.tokens.verify import TokenVerifier
from src.storage import FakeStorage
from src.vault import FakeVault
from src.wiring import Services, assemble

APP_ID = "softbox"
TOOLS = ("karbon-admin", "soneeva-voice")
KID = "test-key"


def keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode(),
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode(),
    )


@pytest.fixture
def keys() -> tuple[str, str]:
    return keypair()


@pytest.fixture
def application(keys) -> dict[str, Any]:
    _, public_pem = keys
    return {
        "name": "Softbox",
        "status": "active",
        "keys": [{"kid": KID, "alg": "EdDSA", "publicKey": public_pem, "status": "active"}],
    }


@pytest.fixture
def verifier(application) -> TokenVerifier:
    return TokenVerifier(KeyStore(FakeDatabase({("apps", APP_ID): application})), ReplayGuard())


@pytest.fixture
def sign(keys):
    private_pem, _ = keys

    def _sign(
        *,
        key: str | None = None,
        alg: str = "EdDSA",
        kid: str | None = KID,
        iss: str = APP_ID,
        sub: str = "store-123",
        aud: str = Audience.API.value,
        exp_delta: int = 60,
        omit: tuple[str, ...] = (),
        jti: str | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": iss,
            "sub": sub,
            "aud": aud,
            "jti": jti or str(uuid.uuid4()),
            "iat": now,
            "exp": now + exp_delta,
        }
        for field in omit:
            claims.pop(field, None)
        return jwt.encode(claims, key or private_pem, algorithm=alg, headers={"kid": kid})

    return _sign


@pytest.fixture
def services() -> Services:
    """Every service, wired as in production, over in-memory fakes."""
    db = FakeDatabase()
    accounts = FakeAccounts()
    accounts.add("owner1", username="owner1")
    built = assemble(
        settings=None, db=db, payment=ByMode(FakePayment(), FakePayment()), accounts=accounts,
        storage=FakeStorage(), vault=FakeVault(), numbers=FakeNumbers(), remote=FakeRemote(),
        reader=FakeReader(),
    )
    built.business_dbs = db.opened  # type: ignore[attr-defined]
    return built


@pytest.fixture
def client(services):
    from fastapi.testclient import TestClient

    from main import attach, create_app

    app = create_app()
    attach(app, services)
    return TestClient(app)  # no `with`: the lifespan would build the real services


@pytest.fixture
def bearer(services):
    """headers(app_id, scopes, sub, uid): gives the app or tool (created if new) a key and scopes, signs
    a hub token. `{appId}_staging` signs with the app's key."""
    private: dict[str, str] = {}

    def headers(app_id: str, scopes: list[str], sub: str = "-", uid: str | None = None,
                lifetime: int = 60) -> dict[str, str]:
        base = app_id.removesuffix("_staging")
        if base not in private:
            private[base], public = keypair()
            table = "tools" if base in TOOLS else "apps"
            row = services.db.rows.setdefault((table, base), {"name": base, "status": "active"})
            row |= {"scopes": [str(s) for s in scopes],
                    "keys": [{"kid": KID, "publicKey": public, "status": "active"}]}
        now = int(time.time())
        claims: dict[str, Any] = {"iss": app_id, "sub": sub, "aud": Audience.HUB.value,
                                  "jti": str(uuid.uuid4()), "iat": now, "exp": now + lifetime}
        if uid:
            claims["uid"] = uid
        token = jwt.encode(claims, private[base], algorithm="EdDSA", headers={"kid": KID})
        return {"Authorization": f"Bearer {token}"}

    return headers
