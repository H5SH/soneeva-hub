"""Business-database indexes: requested on Firestore, waited for, and only then `provisioned`."""

from types import SimpleNamespace

import pytest
from google.cloud.firestore_admin_v1.types import Field, Index

from src.db import BUSINESS_INDEXES, IndexesBuilding
from src.db.firestore import FirestoreDatabase
from src.tunables import tunables
from tests.test_businesses import CONSOLE
from tests.test_plans import invoke
from tests.test_signup_async import signup, status  # noqa: F401 - fixture

FIELD = "projects/proj/databases/b-abc/collectionGroups/calls/fields/startedAt"


class Client:
    """Firestore admin: the field's indexes in `state`, or its automatic config before any request."""

    def __init__(self, state: Index.State | None = None) -> None:
        self.requests: list = []
        self.state = state

    async def get_field(self, name):
        indexes = [Index(query_scope=Index.QueryScope[s], state=self.state,
                         fields=[Index.IndexField(field_path="startedAt", order=Index.IndexField.Order[o])])
                   for s, o in BUSINESS_INDEXES[0].wanted()] if self.state else []
        return Field(name=name, index_config=Field.IndexConfig(indexes=indexes))

    async def update_field(self, request):
        self.requests.append(request)
        self.state = Index.State.CREATING
        return SimpleNamespace()


def firestore(client: Client) -> FirestoreDatabase:
    db = FirestoreDatabase(project="proj", location="us-central1")
    db._admin = client  # type: ignore[assignment]
    return db


async def test_requests_calls_by_start_time_in_both_scopes():
    db = firestore(client := Client())
    await db.request_indexes("b-abc", BUSINESS_INDEXES)
    (calls,) = client.requests
    assert calls.field.name == FIELD and list(calls.update_mask.paths) == ["index_config"]
    scopes = {(i.query_scope, i.fields[0].order) for i in calls.field.index_config.indexes}
    assert len(scopes) == 4
    assert (Index.QueryScope.COLLECTION_GROUP, Index.IndexField.Order.ASCENDING) in scopes
    await db.request_indexes("b-abc", BUSINESS_INDEXES)  # building: not requested again
    assert len(client.requests) == 1


async def test_waits_until_every_index_is_ready():
    db = firestore(client := Client(Index.State.CREATING))
    assert not await db.indexes_ready("b-abc", BUSINESS_INDEXES)
    with pytest.raises(IndexesBuilding):
        await db.wait_for_indexes("b-abc", BUSINESS_INDEXES, timeout=0.05, interval=0.01)
    client.state = Index.State.READY
    await db.wait_for_indexes("b-abc", BUSINESS_INDEXES, timeout=1, interval=0.01)


def test_provisioned_only_after_the_indexes_are_ready(signup, client, bearer, services, monkeypatch):  # noqa: F811
    monkeypatch.setitem(tunables()["businesses"], "indexWaitSeconds", 0)
    services.db.building = building = set()

    async def create_database(name):  # a new database's indexes take a while
        if name not in services.db.databases:
            building.add(name)
            services.db.databases.add(name)

    services.db.create_database = create_database
    business_id = signup().json()["id"]
    pending = status(client, bearer, business_id)
    assert pending["provisioned"] is False and pending["provisionError"] == "indexes still building"
    assert services.accounts.claims[pending["ownerUid"]]["businessId"] == business_id  # the rest is done
    assert ("branches", "main") in services.business_dbs[pending["dbId"]].rows

    building.clear()  # built meanwhile: the retry only waits again
    retry = client.post(f"/v1/businesses/{business_id}/provision",
                        headers=bearer("soneeva", CONSOLE, sub=business_id))
    assert retry.status_code == 202
    done = status(client, bearer, business_id)
    assert done["provisioned"] is True and done["provisionError"] is None


def test_a_provisioned_business_gets_its_missing_indexes(signup, services):  # noqa: F811
    business = signup().json()
    services.db.indexed.clear()  # provisioned before the index existed
    invoke(services, "business:provision", business["id"])
    assert services.db.indexed[business["dbId"]] == set(BUSINESS_INDEXES)
