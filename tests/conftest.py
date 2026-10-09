import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.models.auth import Audience
from app.models.db import Collection
from app.services.db.base import Database
from app.services.tokens.keys import KeyStore
from app.services.tokens.replay import ReplayGuard
from app.services.tokens.verify import TokenVerifier

APP_ID = "softbox"
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


class FakeDatabase(Database):
    """In-memory stand-in. The auth path only ever reads, so the rest can stay minimal."""

    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {
            (Collection.APPLICATIONS.value, k): v for k, v in (rows or {}).items()
        }

    async def get_rows(self, collection, filters=None, limit=None, order_by=None, descending=False):
        return [v for (c, _), v in self.rows.items() if c == collection.value]

    async def get_row(self, collection, row_id):
        return self.rows.get((collection.value, row_id))

    async def create_row(self, collection, row_id, data):
        if (collection.value, row_id) in self.rows:
            return False
        self.rows[(collection.value, row_id)] = data
        return True

    async def set_row(self, collection, row_id, data):
        # Merges, like the real one — a partial write must not wipe the other fields.
        row = self.rows.setdefault((collection.value, row_id), {})
        row.update(data)
        return row

    async def delete_row(self, collection, row_id):
        return self.rows.pop((collection.value, row_id), None) is not None


@pytest.fixture
def keys() -> tuple[str, str]:
    return keypair()


@pytest.fixture
def application(keys) -> dict[str, Any]:
    _, public_pem = keys
    return {
        "name": "Softbox",
        "status": "active",
        "keys": [{"kid": KID, "alg": "EdDSA", "public_key": public_pem, "status": "active"}],
    }


@pytest.fixture
def verifier(application) -> TokenVerifier:
    return TokenVerifier(KeyStore(FakeDatabase({APP_ID: application})), ReplayGuard())


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
