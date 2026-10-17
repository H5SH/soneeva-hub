"""The indexes every business database `b-{id}` needs, provider-neutral. The one source of truth:
provisioning requests them and waits for them; nothing else (the console included) declares any.

Collection-scope single-field indexes are automatic, so only collection-group queries need one here.
A field's entry replaces its automatic configuration, hence collection scope is listed too.
Queries run against a business database (console lib/data/business, the hub, soneeva-voice):
- console Home (facts.ts): every branch's `calls`, `startedAt >=` and `orderBy startedAt desc`;
- everything else (callers by `lastCallAt`/`phone`/`nameLower`, orders and complaints by `createdAt`,
  equality or array-contains with the document id) is collection scope: automatic.
"""

from dataclasses import dataclass

from src.models.db import Sub

ORDERS = ("ASCENDING", "DESCENDING")
SCOPES = ("COLLECTION", "COLLECTION_GROUP")


@dataclass(frozen=True)
class FieldIndex:
    """Single-field indexes on `group.field`: every order, in every scope."""

    group: str
    field: str
    orders: tuple[str, ...] = ORDERS
    scopes: tuple[str, ...] = SCOPES

    def wanted(self) -> set[tuple[str, str]]:
        """(scope, order) pairs."""
        return {(scope, order) for scope in self.scopes for order in self.orders}


BUSINESS_INDEXES: tuple[FieldIndex, ...] = (FieldIndex(Sub.CALLS, "startedAt"),)
