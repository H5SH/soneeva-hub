"""The database's plain values: paths, queries, documents and writes."""

from dataclasses import dataclass, replace
from typing import Any

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
