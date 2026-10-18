"""Where a business's data lives, from its `dbId` (soneeva-voice and the console read it the same way):

- none: nested in `(default)`, under `businesses/{id}`;
- `{region}/{id}`: nested in the region's shared database `region-{region}`, under `businesses/{id}`;
- anything else: a database of its own by that name (`b-{id}`, a custom location).

A nested business sees only its own subtree: every path gets its root, and a collection-group query
(which spans the whole database) also filters on `businessId`, written on every document it reads.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.db.base import Database, Doc, Query, Transaction, Write
from src.db.base import path as join
from src.db.indexes import DbIndex
from src.models.db import Default

#: The shared database of region `eu` is `region-eu` (a database id needs four characters at least).
REGION_PREFIX = "region-"
#: Written on every collection-group document of a nested business; its group queries filter on it.
OWNER_FIELD = "businessId"


@dataclass(frozen=True)
class Place:
    #: None: `(default)`.
    database: str | None
    #: `businesses/{id}` when nested, else "".
    root: str


def place(business_id: str, db_id: str | None) -> Place:
    nested = join(Default.BUSINESSES, business_id)
    if not db_id:
        return Place(None, nested)
    region, slash, _ = db_id.partition("/")
    return Place(REGION_PREFIX + region, nested) if slash else Place(db_id, "")


def region_db(region: str) -> str:
    return REGION_PREFIX + region


class _Tx(Transaction):
    def __init__(self, tx: Transaction, root: str) -> None:
        self._tx, self._root = tx, root

    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        return await self._tx.get(join(self._root, path), doc_id)

    def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None:
        self._tx.set(join(self._root, path), doc_id, data, merge)


class Nested(Database):
    """One business's subtree of a shared database, as if it were a database of its own."""

    def __init__(self, db: Database, root: str, business_id: str) -> None:
        self._db, self._root, self._business_id = db, root, business_id

    def _at(self, path: str) -> str:
        return join(self._root, path)

    def _scoped(self, query: Query) -> Query:
        if query.group:
            return query.where(OWNER_FIELD, "==", self._business_id)
        return replace(query, path=self._at(query.path))

    # ── databases: a nested business has none of its own ─────────────────────
    def open(self, name: str) -> Database:
        return self._db.open(name)

    async def database_exists(self, name: str) -> bool:
        return await self._db.database_exists(name)

    async def create_database(self, name: str, location: str | None = None) -> None:
        await self._db.create_database(name, location)

    async def locations(self) -> list[dict[str, str]]:
        return await self._db.locations()

    async def request_indexes(self, name: str, indexes: Sequence[DbIndex]) -> None:
        await self._db.request_indexes(name, indexes)

    async def indexes_ready(self, name: str, indexes: Sequence[DbIndex]) -> bool:
        return await self._db.indexes_ready(name, indexes)

    async def collections(self) -> list[str]:
        raise NotImplementedError("A nested business has no top-level collections")

    async def drop_collection(self, name: str) -> None:
        raise NotImplementedError("A nested business has no top-level collections")

    # ── documents ────────────────────────────────────────────────────────────
    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        return await self._db.get(self._at(path), doc_id)

    async def get_all(self, path: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._db.get_all(self._at(path), ids)

    async def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None:
        await self._db.set(self._at(path), doc_id, data, merge)

    async def create(self, path: str, doc_id: str, data: dict[str, Any]) -> bool:
        return await self._db.create(self._at(path), doc_id, data)

    async def query(self, query: Query) -> list[Doc]:
        return await self._db.query(self._scoped(query))

    async def count(self, query: Query) -> int:
        return await self._db.count(self._scoped(query))

    async def sum(self, query: Query, fields: list[str]) -> dict[str, float]:
        return await self._db.sum(self._scoped(query), fields)

    async def batch(self, writes: list[Write]) -> None:
        await self._db.batch([replace(w, path=self._at(w.path)) for w in writes])

    async def transaction[T](self, run: Callable[[Transaction], Awaitable[T]]) -> T:
        return await self._db.transaction(lambda tx: run(_Tx(tx, self._root)))


def business_db(db: Database, business_id: str, db_id: str | None) -> Database:
    """The business's data in `db`'s project, nested or a database of its own."""
    where = place(business_id, db_id)
    shared = db.open(where.database) if where.database else db
    return Nested(shared, where.root, business_id) if where.root else shared
