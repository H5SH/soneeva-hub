"""A rotated key must verify at once, not after the KeyStore's 60s cache has expired."""

import pytest

from app.models.auth import Audience
from app.services.admin import AdminService
from app.services.roles import RoleStore
from app.services.tokens.keys import KeyStore
from app.services.tokens.replay import ReplayGuard
from app.services.tokens.verify import TokenVerifier
from tests.conftest import APP_ID, FakeDatabase, FakePayment


@pytest.fixture
def wired(application):
    db = FakeDatabase({APP_ID: dict(application)})
    store = KeyStore(db)
    return store, AdminService(db, store, RoleStore(db), FakePayment()), TokenVerifier(store, ReplayGuard())


async def test_rotated_key_verifies_immediately(wired, sign):
    store, admin, verifier = wired
    # Warm the cache, so a stale copy is what verification would otherwise read.
    assert await store.get(APP_ID) is not None

    kid, private_pem = await admin.rotate_key(APP_ID)
    claims = await verifier.verify(sign(key=private_pem, kid=kid), Audience.API)
    assert claims.iss == APP_ID


async def test_deleted_app_stops_verifying_immediately(wired):
    store, admin, _ = wired
    assert await store.get(APP_ID) is not None

    await admin.delete_app(APP_ID)
    assert await store.get(APP_ID) is None
