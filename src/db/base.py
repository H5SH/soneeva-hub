"""The one database contract. A path names a collection, slash-joined: `callers`, `callers/{phone}/calls`.

Raw documents (get, set, query, …) are plain dicts as stored. Rows (get_row, set_row, …) add the
soft delete and timestamps; a document without the flag (the console writes some directly) is live.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from src.db.indexes import DbIndex
from src.models.db import CREATED_AT, DELETED_AT, DELETED_FLAG, UPDATED_AT

#: Order by (or filter on) the document id; Firestore's own name for it.
ID = "__name__"


def path(*parts: str) -> str:
    return "/".join(parts)


class Delete:
    """In a merged `set`: remove this field."""


DELETE = Delete()


@dataclass(frozen=True)
class Increment:
    amount: int


class DatabaseError(Exception):
    """A database could not be created or configured."""


class IndexesBuilding(DatabaseError):
    """The indexes were requested but are not all ready yet; waiting again resumes."""


@dataclass(frozen=True)
class Query:
    """`where` ops: == < <= > >= in array_contains. `after` is a document id in `path`.
    `group`: every collection named `path`, at any depth."""

    path: str
    filters: tuple[tuple[str, str, Any], ...] = ()
    order: tuple[tuple[str, bool], ...] = ()
    limit: int | None = None
    after: str | None = None
    group: bool = False

    def where(self, field: str, op: str, value: Any) -> "Query":
        return replace(self, filters=(*self.filters, (field, op, value)))

    def order_by(self, field: str, descending: bool = False) -> "Query":
        return replace(self, order=(*self.order, (field, descending)))

    def take(self, limit: int) -> "Query":
        return replace(self, limit=limit)

    def start_after(self, doc_id: str) -> "Query":
        return replace(self, after=doc_id)


@dataclass
class Doc:
    id: str
    data: dict[str, Any]
    #: The id of the document whose subcollection holds this one.
    parent: str | None = None


@dataclass(frozen=True)
class Write:
    """`data` None deletes the document."""

    path: str
    doc_id: str
    data: dict[str, Any] | None = None
    merge: bool = False


class Transaction(ABC):
    @abstractmethod
    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None: ...


def live_rows(docs: list[Doc], limit: int | None = None) -> list[dict[str, Any]]:
    """Rows not flagged deleted, with their `id`, up to `limit`."""
    rows = [d.data | {"id": d.id} for d in docs if not d.data.get(DELETED_FLAG)]
    return rows[:limit] if limit else rows


class Database(ABC):
    # ── databases ────────────────────────────────────────────────────────────
    @abstractmethod
    def open(self, name: str) -> "Database":
        """Another database of the project, e.g. a business's `b-{id}`."""

    @abstractmethod
    async def database_exists(self, name: str) -> bool: ...

    @abstractmethod
    async def create_database(self, name: str, location: str | None = None) -> None:
        """Nothing when it exists already. `location` (a Firestore location id) or the project's own."""

    @abstractmethod
    async def locations(self) -> list[dict[str, str]]:
        """Where a database can be made, as `{id, name}` (`europe-west3`, "Frankfurt"), by name."""

    @abstractmethod
    async def request_indexes(self, name: str, indexes: Sequence[DbIndex]) -> None:
        """Asks for `indexes` on database `name`, skipping those already there or building.
        Returns once requested; they build afterwards (minutes on a new database)."""

    @abstractmethod
    async def indexes_ready(self, name: str, indexes: Sequence[DbIndex]) -> bool:
        """Whether every one of `indexes` on database `name` is built and serving queries."""

    async def wait_for_indexes(self, name: str, indexes: Sequence[DbIndex], timeout: float,
                               interval: float = 10) -> None:
        """Polls until every one of `indexes` is ready; IndexesBuilding after `timeout` seconds."""
        deadline = monotonic() + timeout
        while not await self.indexes_ready(name, indexes):
            if monotonic() >= deadline:
                raise IndexesBuilding("indexes still building")
            await asyncio.sleep(max(0.0, min(interval, deadline - monotonic())))

    @abstractmethod
    async def collections(self) -> list[str]:
        """Top-level collection names."""

    @abstractmethod
    async def drop_collection(self, name: str) -> None:
        """Deletes a top-level collection and everything under it, for good."""

    # ── documents ────────────────────────────────────────────────────────────
    @abstractmethod
    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_all(self, path: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """The ones that exist, by id."""

    @abstractmethod
    async def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None:
        """Values may be DELETE or Increment when merging."""

    @abstractmethod
    async def create(self, path: str, doc_id: str, data: dict[str, Any]) -> bool:
        """False when it exists already."""

    @abstractmethod
    async def query(self, query: Query) -> list[Doc]: ...

    @abstractmethod
    async def count(self, query: Query) -> int: ...

    @abstractmethod
    async def sum(self, query: Query, fields: list[str]) -> dict[str, float]:
        """Each (dotted) field summed over the matches."""

    @abstractmethod
    async def batch(self, writes: list[Write]) -> None: ...

    @abstractmethod
    async def transaction[T](self, run: Callable[[Transaction], Awaitable[T]]) -> T:
        """`run` reads then writes; retried when the documents it read changed meanwhile."""

    # ── rows: soft delete and timestamps ─────────────────────────────────────
    async def get_rows(self, collection: str, filters: dict[str, Any] | None = None,
                       limit: int | None = None) -> list[dict[str, Any]]:
        """Deleted rows are dropped here, not in the query: a missing field cannot be matched."""
        query = Query(collection)
        for field, value in (filters or {}).items():
            query = query.where(field, "==", value)
        return live_rows(await self.query(query), limit)

    async def get_row(self, collection: str, row_id: str) -> dict[str, Any] | None:
        row = await self.get(collection, row_id)
        return None if row is None or row.get(DELETED_FLAG) else row | {"id": row_id}

    async def create_row(self, collection: str, row_id: str, data: dict[str, Any]) -> bool:
        """Insert only. False when the id already exists — used for webhook idempotency."""
        now = datetime.now(UTC)
        stamps = {DELETED_FLAG: False, DELETED_AT: None, CREATED_AT: now, UPDATED_AT: now}
        return await self.create(collection, row_id, data | stamps)

    async def set_row(self, collection: str, row_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Upsert, merging into any existing row. Returns the written payload."""
        now = datetime.now(UTC)
        payload = data | {DELETED_FLAG: False, DELETED_AT: None, UPDATED_AT: now}
        if await self.get(collection, row_id) is None:
            payload[CREATED_AT] = now
        await self.set(collection, row_id, payload)
        return payload | {"id": row_id}

    async def delete_row(self, collection: str, row_id: str) -> bool:
        """Soft delete. False when the row does not exist."""
        if await self.get(collection, row_id) is None:
            return False
        now = datetime.now(UTC)
        await self.set(collection, row_id, {DELETED_FLAG: True, DELETED_AT: now, UPDATED_AT: now})
        return True

    async def claim(self, collection: str, row_id: str, field: str,
                    extra: dict[str, Any] | None = None) -> bool:
        """Atomically set `field` true (plus `extra`) only if the row exists and it is not yet.
        False when missing, deleted or already claimed: what makes a one-time code one-time."""

        async def run(tx: Transaction) -> bool:
            row = await tx.get(collection, row_id)
            if row is None or row.get(DELETED_FLAG) or row.get(field):
                return False
            tx.set(collection, row_id, {field: True, UPDATED_AT: datetime.now(UTC)} | (extra or {}))
            return True

        return await self.transaction(run)
