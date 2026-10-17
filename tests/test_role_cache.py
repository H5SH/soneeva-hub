"""An edited role must apply on the next request, not when the cache happens to expire."""

import pytest

from src.models.admin import ROLE_SCOPES, SUPERADMIN, Scope
from src.models.db import Default
from src.models.people import Actor
from src.services.people.roles import RoleService
from src.services.people.store import People
from src.services.roles import RoleStore
from tests.conftest import FakeDatabase

ROLE = "auditor"
BOSS = Actor(app_id="karbon-admin", sub="-", uid="boss", platform=True, role="boss",
             scopes=["*", *[str(s) for s in ROLE_SCOPES]])


@pytest.fixture
def wired():
    db = FakeDatabase()
    db.rows[(Default.ROLES.value, ROLE)] = {"scopes": [str(Scope.APP_VIEW)]}
    roles = RoleStore(db)
    return db, roles, RoleService(People(db), roles)


async def test_cached_role_is_not_a_second_read(wired):
    db, roles, _ = wired
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]
    db.rows.pop((Default.ROLES.value, ROLE))  # gone from the database, still cached
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]


async def test_edited_role_applies_immediately(wired):
    _, roles, service = wired
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]
    await service.update(BOSS, ROLE, [str(Scope.APP_VIEW), str(Scope.KEY_ROTATE)])
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW, Scope.KEY_ROTATE]


async def test_deleted_role_applies_immediately(wired):
    _, roles, service = wired
    assert await roles.scopes(ROLE) == [Scope.APP_VIEW]
    await service.delete(BOSS, ROLE)
    assert await roles.scopes(ROLE) == []


async def test_unknown_role_falls_back_to_the_defaults(wired):
    _, roles, _ = wired
    assert await roles.scopes(SUPERADMIN) == ROLE_SCOPES
    assert await roles.scopes("") == []
