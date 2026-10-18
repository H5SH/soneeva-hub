"""In memory, for tests: queries, merges, cursors and transactions as Firestore does them."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

from src.db.base import DELETE, ID, Database, Doc, Increment, Query, Transaction, Write
from src.db.indexes import DbIndex

MISSING: Any = object()
#: Firestore orders values by type first; a range filter matches only values of its own type.
RANKS: list[type | tuple[type, ...]] = [type(None), bool, (int, float), datetime, str]


def _rank(value: Any) -> int:
    return next((i for i, kind in enumerate(RANKS) if isinstance(value, kind)), len(RANKS))


def _key(value: Any) -> tuple[int, Any]:
    return _rank(value), value if _rank(value) < len(RANKS) else str(value)


def _ranged(test: Callable[[Any, Any], bool]) -> Callable[[Any, Any], bool]:
    return lambda a, b: _rank(a) == _rank(b) and test(a, b)


OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b, "<": _ranged(lambda a, b: a < b), "<=": _ranged(lambda a, b: a <= b),
    ">": _ranged(lambda a, b: a > b), ">=": _ranged(lambda a, b: a >= b), "in": lambda a, b: a in b,
    "array_contains": lambda a, b: isinstance(a, list) and b in a,
}


def snake_keys(value: Any, at: str = "") -> list[str]:
    """Every snake_case field name in a document, nested ones too."""
    if isinstance(value, dict):
        return [f"{at}{k}" for k in value if "_" in k] + [
            name for k, v in value.items() for name in snake_keys(v, f"{at}{k}.")]
    return [name for v in value for name in snake_keys(v, at)] if isinstance(value, list) else []


def camel_only(data: dict[str, Any]) -> dict[str, Any]:
    """Every write is camelCase on disk: the fake refuses anything else, so a test catches it."""
    assert not snake_keys(data), f"snake_case field written: {snake_keys(data)}"
    return data


def _field(doc_id: str, data: dict[str, Any], field: str) -> Any:
    if field == ID:
        return doc_id
    value: Any = data
    for part in field.split("."):
        value = value.get(part, MISSING) if isinstance(value, dict) else MISSING
    return value


def _merge(old: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    row = dict(old)
    for key, value in camel_only(data).items():
        if value is DELETE:
            row.pop(key, None)
        elif isinstance(value, Increment):
            row[key] = (row.get(key) or 0) + value.amount
        else:
            row[key] = value
    return row


class _Tx(Transaction):
    def __init__(self, db: "FakeDatabase") -> None:
        self._db, self.writes = db, list[Write]()

    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        return await self._db.get(path, doc_id)

    def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None:
        self.writes.append(Write(path, doc_id, data, merge))


class FakeDatabase(Database):
    """`rows[(path, id)]` is each document; `opened` the other databases, `indexed` their indexes,
    ready at once unless the database is in `building`."""

    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = dict(rows or {})
        self.opened: dict[str, FakeDatabase] = {}
        self.databases: set[str] = set()
        #: Where each database made here went (None: the project's own location).
        self.placed: dict[str, str | None] = {}
        self.indexed: dict[str, set[DbIndex]] = {}
        self.building: set[str] = set()

    def open(self, name: str) -> "FakeDatabase":
        return self.opened.setdefault(name, FakeDatabase())

    async def database_exists(self, name: str) -> bool:
        return name in self.databases

    async def collections(self) -> list[str]:
        return sorted({path.split("/")[0] for path, _ in self.rows})

    async def drop_collection(self, name: str) -> None:
        self.rows = {k: v for k, v in self.rows.items() if k[0].split("/")[0] != name}

    async def create_database(self, name: str, location: str | None = None) -> None:
        if name not in self.databases:
            self.databases.add(name)
            self.placed[name] = location

    async def locations(self) -> list[dict[str, str]]:
        return [{"id": "europe-west3", "name": "Frankfurt"}, {"id": "nam5", "name": "United States"},
                {"id": "northamerica-northeast1", "name": "Montréal"}]

    async def request_indexes(self, name: str, indexes: Sequence[DbIndex]) -> None:
        self.indexed.setdefault(name, set()).update(indexes)

    async def indexes_ready(self, name: str, indexes: Sequence[DbIndex]) -> bool:
        return name not in self.building and set(indexes) <= self.indexed.get(name, set())

    async def get(self, path: str, doc_id: str) -> dict[str, Any] | None:
        row = self.rows.get((path, doc_id))
        return dict(row) if row is not None else None

    async def get_all(self, path: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {i: dict(self.rows[(path, i)]) for i in ids if (path, i) in self.rows}

    async def set(self, path: str, doc_id: str, data: dict[str, Any], merge: bool = True) -> None:
        self.rows[(path, doc_id)] = _merge(self.rows.get((path, doc_id), {}) if merge else {}, data)

    async def create(self, path: str, doc_id: str, data: dict[str, Any]) -> bool:
        if (path, doc_id) in self.rows:
            return False
        await self.set(path, doc_id, data)
        return True

    def _matches(self, query: Query) -> list[Doc]:
        docs: list[Doc] = []
        for (at, doc_id), data in self.rows.items():
            parts = at.split("/")
            if (parts[-1] if query.group else at) != query.path:
                continue
            doc = Doc(doc_id, dict(data), parts[-2] if len(parts) > 2 else None)
            values = [_field(doc_id, data, f) for f, _, _ in query.filters]
            if all(v is not MISSING and OPS[op](v, want)
                   for v, (_, op, want) in zip(values, query.filters, strict=True)):
                docs.append(doc)
        return docs

    async def query(self, query: Query) -> list[Doc]:
        docs = self._matches(query)
        docs = [d for d in docs if all(_field(d.id, d.data, f) is not MISSING for f, _ in query.order)]
        docs.sort(key=lambda d: d.id, reverse=bool(query.order) and query.order[-1][1])
        for field, descending in reversed(query.order):
            docs.sort(key=lambda d, f=field: _key(_field(d.id, d.data, f)), reverse=descending)
        ids = [d.id for d in docs]
        if query.after in ids:
            docs = docs[ids.index(query.after) + 1:]
        return docs[:query.limit] if query.limit else docs

    async def count(self, query: Query) -> int:
        return len(await self.query(query))

    async def sum(self, query: Query, fields: list[str]) -> dict[str, float]:
        docs = await self.query(query)
        values = {f: [_field(d.id, d.data, f) for d in docs] for f in fields}
        return {f: float(sum(v for v in vs if isinstance(v, int | float))) for f, vs in values.items()}

    async def batch(self, writes: list[Write]) -> None:
        for w in writes:
            if w.data is None:
                self.rows.pop((w.path, w.doc_id), None)
            else:
                await self.set(w.path, w.doc_id, w.data, w.merge)

    async def transaction[T](self, run: Callable[[Transaction], Awaitable[T]]) -> T:
        tx = _Tx(self)
        result = await run(tx)
        await self.batch(tx.writes)
        return result
