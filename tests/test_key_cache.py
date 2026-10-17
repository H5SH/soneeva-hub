"""A rotated key must verify at once, not after the KeyStore's 60s cache has expired; an app edit
applies at once, not after the AppRegistry's TTL."""

import pytest

from src.models.auth import Audience
from src.payment import FakePayment
from src.services.admin import AdminService
from src.services.modes import ByMode
from src.services.plans import PlanService
from src.services.tokens.keys import KeyStore
from src.services.tokens.replay import ReplayGuard
from src.services.tokens.verify import TokenVerifier
from tests.conftest import APP_ID, FakeDatabase


@pytest.fixture
def wired(application):
    db = FakeDatabase({("apps", APP_ID): dict(application)})
    store = KeyStore(db)
    payment = ByMode(FakePayment())
    plans = payment.map(lambda gateway, _: PlanService(db, gateway))
    admin = AdminService(db, store, payment, plans)
    return store, admin, TokenVerifier(store, ReplayGuard())


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


async def test_every_app_or_plan_write_clears_the_app_registry(services):
    registry = services.registry
    await services.admin.create_app("cache-app", "Cache", "", [])
    assert (await registry.get("cache-app")).name == "Cache"  # cached now
    await services.admin.update_app("cache-app", name="Renamed")
    assert (await registry.get("cache-app")).name == "Renamed"
    await services.plans.live.define("cache-app", "free", "Free")
    assert [p.code for p in (await registry.get("cache-app")).plans] == ["free"]
    await services.admin.delete_app("cache-app")
    assert await registry.get("cache-app") is None
