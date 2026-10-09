import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.models.auth import Audience
from app.models.db import DELETED_FLAG, Collection
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
        # `| {"id": ...}` mirrors the real one: Firestore keeps the document id outside the
        # document, and callers rely on it being folded in.
        out = [
            v | {"id": row_id}
            for (c, row_id), v in self.rows.items()
            if c == collection.value and not v.get(DELETED_FLAG)
        ]
        for field, value in (filters or {}).items():
            out = [r for r in out if r.get(field) == value]
        return out[:limit] if limit else out

    async def get_row(self, collection, row_id):
        row = self.rows.get((collection.value, row_id))
        if row is None or row.get(DELETED_FLAG):
            return None
        return row | {"id": row_id}

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
        # Soft, like the real one: the row stays and reads start returning None.
        row = self.rows.get((collection.value, row_id))
        if row is None or row.get(DELETED_FLAG):
            return False
        row[DELETED_FLAG] = True
        return True


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


class FakePayment:
    """Stripe stand-in. Records what it was asked to create so a test can assert on it."""

    def __init__(self) -> None:
        self.prices: list[tuple[str, str, int]] = []
        self.discounts: list[tuple[str, list[str]]] = []
        self.writable = True

    async def can_write_products(self) -> tuple[bool, str]:
        return (True, "") if self.writable else (False, "test mode refused")

    async def create_price(self, app_id, code, name, amount, currency, interval):
        self.prices.append((app_id, code, amount))
        return f"prod_{app_id}_{code}", f"price_{app_id}_{code}"

    async def create_full_discount(self, code, app_id, product_ids, max_redemptions):
        self.discounts.append((code, product_ids))
        return f"coup_{code}", f"promo_{code}"

    async def delete_promotion_code(self, promotion_code_id) -> None: ...
