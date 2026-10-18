"""The only module that speaks Firestore. Credentials: GOOGLE_APPLICATION_CREDENTIALS or the GCP runtime."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from functools import cached_property
from typing import Any

import firebase_admin
import google.auth
from firebase_admin import firestore_async
from google.api_core.exceptions import AlreadyExists, NotFound
from google.auth.transport.requests import AuthorizedSession
from google.cloud import firestore_v1 as fs
from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminAsyncClient
from google.cloud.firestore_admin_v1.types import Database as AdminDatabase
from google.cloud.firestore_admin_v1.types import Field, Index, UpdateFieldRequest
from google.protobuf.field_mask_pb2 import FieldMask

from src.db.base import DELETE, Database, DatabaseError, Doc, Increment, Query, Transaction, Write
from src.db.indexes import CompositeIndex, DbIndex, FieldIndex

#: An index requested again while it builds is left alone; one that needs repair is requested again.
LIVE_STATES = {Index.State.READY, Index.State.CREATING}
API, SCOPE = "https://firestore.googleapis.com/v1", "https://www.googleapis.com/auth/datastore"
#: A new database is usually ready in seconds; the operation's own polling never gives up.
CREATE_TIMEOUT_SECONDS = 300


def _value(value: Any) -> Any:
    if value is DELETE:
        return fs.DELETE_FIELD
    return fs.Increment(value.amount) if isinstance(value, Increment) else value


def _data(data: dict[str, Any]) -> dict[str, Any]:
    return {k: _value(v) for k, v in data.items()}


def _doc(snapshot: Any) -> Doc:
    owner = snapshot.reference.parent.parent
    return Doc(snapshot.id, snapshot.to_dict() or {}, owner.id if owner else None)


class _Tx(Transaction):
    def __init__(self, db: "FirestoreDatabase", tx: fs.AsyncTransaction) -> None:
        self._db, self._tx = db, tx

    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        snapshot = await self._db.ref(path, doc_id).get(transaction=self._tx)
        return (snapshot.to_dict() or {}) if snapshot.exists else None

    def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None:
        self._tx.set(self._db.ref(path, doc_id), _data(data), merge=merge)


class FirestoreDatabase(Database):
    """`name` picks a named database, e.g. a business's `b-{id}`; None is `(default)`.
    `project` and `location` are where new databases go (roles/datastore.owner)."""

    def __init__(self, name: str | None = None, project: str = "", location: str = "") -> None:
        self._database, self._project, self._location = name, project, location
        self._opened: dict[str, FirestoreDatabase] = {}
        self._admin: FirestoreAdminAsyncClient | None = None

    @cached_property
    def _client(self) -> fs.AsyncClient:
        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app()
        return firestore_async.client(database_id=self._database) if self._database \
            else firestore_async.client()

    def ref(self, path: str, doc_id: str) -> Any:
        return self._client.collection(path).document(doc_id)

    def open(self, name: str) -> "FirestoreDatabase":
        if name not in self._opened:
            self._opened[name] = FirestoreDatabase(name, self._project, self._location)
        return self._opened[name]

    # ── databases ────────────────────────────────────────────────────────────
    @property
    def admin(self) -> FirestoreAdminAsyncClient:
        self._admin = self._admin or FirestoreAdminAsyncClient()
        return self._admin

    def _name(self, name: str) -> str:
        return f"projects/{self._project}/databases/{name}"

    async def database_exists(self, name: str) -> bool:
        try:
            await self.admin.get_database(name=self._name(name))
        except NotFound:
            return False
        return True

    async def collections(self) -> list[str]:
        return sorted([c.id async for c in self._client.collections()])

    async def drop_collection(self, name: str) -> None:
        await self._client.recursive_delete(self._client.collection(name))

    async def create_database(self, name: str, location: str | None = None) -> None:
        location = location or self._location
        if not self._project or not location:
            raise DatabaseError("No Google Cloud project in the credentials, or no cloud.region")
        native = AdminDatabase.DatabaseType.FIRESTORE_NATIVE
        database = AdminDatabase(location_id=location, type_=native)
        try:
            operation = await self.admin.create_database(
                parent=f"projects/{self._project}", database=database, database_id=name)
            await operation.result(timeout=CREATE_TIMEOUT_SECONDS)
        except AlreadyExists:
            return
        except Exception as error:
            raise DatabaseError(f"Could not create {name}: {error}") from error

    def _field(self, name: str, index: FieldIndex) -> str:
        return f"{self._name(name)}/collectionGroups/{index.group}/fields/{index.field}"

    async def _states(self, name: str, index: FieldIndex) -> dict[tuple[str, str], Index.State]:
        """(scope, order) → state of the field's single-field indexes as configured now."""
        try:
            field = await self.admin.get_field(name=self._field(name, index))
        except NotFound:
            return {}
        if field.index_config.reverting:
            return {}
        return {(i.query_scope.name, i.fields[0].order.name): i.state
                for i in field.index_config.indexes if len(i.fields) == 1}

    async def _composite(self, name: str, index: CompositeIndex) -> Index.State | None:
        """The state of the composite index as declared, None when there is none."""
        parent = f"{self._name(name)}/collectionGroups/{index.group}"
        async for found in await self.admin.list_indexes(parent=parent):
            fields = [(f.field_path, f.order.name) for f in found.fields if f.field_path != "__name__"]
            if found.query_scope.name == index.scope and tuple(fields) == index.fields:
                return found.state
        return None

    async def _request_composite(self, name: str, index: CompositeIndex) -> None:
        if await self._composite(name, index) in LIVE_STATES:
            return
        fields = [Index.IndexField(field_path=f, order=Index.IndexField.Order[o]) for f, o in index.fields]
        try:  # not awaiting the operation: wait_for_indexes polls instead
            scope = Index.QueryScope[index.scope]
            await self.admin.create_index(parent=f"{self._name(name)}/collectionGroups/{index.group}",
                                          index=Index(query_scope=scope, fields=fields))
        except AlreadyExists:
            return
        except Exception as error:
            where = f"{index.group}{list(index.fields)} on {name}"
            raise DatabaseError(f"Could not index {where}: {error}") from error

    async def request_indexes(self, name: str, indexes: Sequence[DbIndex]) -> None:
        for index in indexes:
            if isinstance(index, CompositeIndex):
                await self._request_composite(name, index)
                continue
            states = await self._states(name, index)
            if all(states.get(pair) in LIVE_STATES for pair in index.wanted()):
                continue
            config = Field(name=self._field(name, index), index_config=Field.IndexConfig(indexes=[
                Index(query_scope=Index.QueryScope[scope],
                      fields=[Index.IndexField(field_path=index.field, order=Index.IndexField.Order[order])])
                for scope, order in sorted(index.wanted())]))
            try:  # not awaiting the operation: wait_for_indexes polls the field instead
                await self.admin.update_field(
                    request=UpdateFieldRequest(field=config, update_mask=FieldMask(paths=["index_config"])))
            except Exception as error:
                where = f"{index.group}.{index.field} on {name}"
                raise DatabaseError(f"Could not index {where}: {error}") from error

    async def indexes_ready(self, name: str, indexes: Sequence[DbIndex]) -> bool:
        for index in indexes:
            if isinstance(index, CompositeIndex):
                if await self._composite(name, index) != Index.State.READY:
                    return False
                continue
            states = await self._states(name, index)
            if any(states.get(pair) != Index.State.READY for pair in index.wanted()):
                return False
        return True

    async def locations(self) -> list[dict[str, str]]:
        def listed() -> list[dict[str, Any]]:
            session = AuthorizedSession(google.auth.default(scopes=[SCOPE])[0])  # pyright: ignore
            found: list[dict[str, Any]] = []
            token = ""
            while True:
                page = session.get(
                    f"{API}/projects/{self._project}/locations", params={"pageToken": token}, timeout=20)
                page.raise_for_status()
                body: dict[str, Any] = page.json()
                found += body.get("locations", [])
                token = body.get("nextPageToken", "")
                if not token:
                    return found

        try:
            rows = await asyncio.to_thread(listed)
        except Exception as error:
            raise DatabaseError(f"Could not list Firestore locations: {error}") from error
        found = [{"id": r["locationId"], "name": r.get("displayName") or r["locationId"]} for r in rows]
        return sorted(found, key=lambda r: r["name"])

    # ── documents ────────────────────────────────────────────────────────────
    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        snapshot = await self.ref(path, doc_id).get()
        return (snapshot.to_dict() or {}) if snapshot.exists else None

    async def get_all(self, path: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        refs = [self.ref(path, i) for i in ids]
        return {s.id: s.to_dict() or {} async for s in self._client.get_all(refs) if s.exists}

    async def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None:
        await self.ref(path, doc_id).set(_data(data), merge=merge)

    async def create(self, path: str, doc_id: str, data: dict[str, Any]) -> bool:
        try:
            await self.ref(path, doc_id).create(data)
        except AlreadyExists:
            return False
        return True

    def _query(self, query: Query) -> Any:
        found: Any = (self._client.collection_group(query.path) if query.group
                      else self._client.collection(query.path))
        for field, op, value in query.filters:
            found = found.where(filter=fs.FieldFilter(field, op, value))
        for field, descending in query.order:
            found = found.order_by(field, direction=fs.Query.DESCENDING if descending else fs.Query.ASCENDING)
        return found.limit(query.limit) if query.limit else found

    async def query(self, query: Query) -> list[Doc]:
        found = self._query(query)
        if query.after:
            after = await self._client.collection(query.path).document(query.after).get()
            if after.exists:
                found = found.start_after(after)
        return [_doc(s) async for s in found.stream()]

    async def count(self, query: Query) -> int:
        result = await self._query(query).count(alias="n").get()
        return int(next((r.value for row in result for r in row), 0) or 0)

    async def sum(self, query: Query, fields: list[str]) -> dict[str, float]:
        found = self._query(query)
        for field in fields:
            found = found.sum(field, alias=field.replace(".", "_"))
        values = {r.alias: r.value for row in await found.get() for r in row}
        return {f: float(values.get(f.replace(".", "_")) or 0) for f in fields}

    async def batch(self, writes: list[Write]) -> None:
        batch = self._client.batch()
        for w in writes:
            if w.data is None:
                batch.delete(self.ref(w.path, w.doc_id))
            else:
                batch.set(self.ref(w.path, w.doc_id), _data(w.data), merge=w.merge)
        await batch.commit()

    async def transaction[T](self, run: Callable[[Transaction], Awaitable[T]]) -> T:
        @fs.async_transactional
        async def attempt(tx: fs.AsyncTransaction) -> T:
            return await run(_Tx(self, tx))

        return await attempt(self._client.transaction())
