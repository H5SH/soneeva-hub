"""An edited role must apply on the next request, not when the cache happens to expire."""

import pytest

from app.models.admin import SUPERADMIN, Scope
from app.models.db import Collection
from app.services.admin import AdminService
from app.services.roles import RoleStore
from app.services.tokens.keys import KeyStore
from tests.conftest import FakeDatabase, FakePayment

ROLE = "auditor"


@pytest.fixture
def wired():
    db = FakeDatabase()
    db.rows[(Collection.ROLES.value, ROLE)] = {"scopes": [str(Scope.APP_VIEW)]}
    roles = RoleStore(db)
    return db, roles, AdminService(db, KeyStore(db), roles, FakePayment())


async def test_cached_role_is_not_a_second_read(wired):
    db, roles, _ = wired
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]
    db.rows.pop((Collection.ROLES.value, ROLE))  # gone from the database, still cached
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]


async def test_edited_role_applies_immediately(wired):
    _, roles, admin = wired
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]

    await admin.save_role(ROLE, [str(Scope.APP_VIEW), str(Scope.KEY_ROTATE)])
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW, Scope.KEY_ROTATE]


async def test_deleted_role_applies_immediately(wired):
    db, roles, admin = wired
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]

    await admin.delete_role(ROLE)
    assert await roles.scopes(ROLE) == []


async def test_unknown_role_falls_back_to_the_defaults(wired):
    _, roles, _ = wired
    assert await roles.scopes(SUPERADMIN) == list(Scope)
    assert await roles.scopes("") == []
