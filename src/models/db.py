"""The registry: every Firestore collection, the fields the database layer adds, and doc ids.

Nothing else in the hub spells a collection name (tests/test_registry.py checks). Every field
of every document the hub writes is camelCase; Python models may keep snake_case attribute
names, so stored models read both spellings (`STORED`) and are written through `to_doc`.
Payment data (plans, coupons, customers, subscriptions, invoices, usage) is never stored: Stripe holds it.

The one exception is demo-owned: `(default)/callers/{phone}/calls/{id}/messages/{n}`, which the
browser demo writes in snake_case. Only src/services/platform/calls.py touches it.
"""

from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, AliasGenerator, ConfigDict

DELETED_FLAG = "isDeleted"
DELETED_AT = "deletedAt"
CREATED_AT = "createdAt"
UPDATED_AT = "updatedAt"


class Default(StrEnum):
    """`(default)`: karbon-hub's own database."""

    APPS = "apps"
    #: Internal tools: hub keys and scopes, never a Stripe link (karbon-admin, soneeva-voice).
    TOOLS = "tools"
    USERS = "users"
    ROLES = "roles"
    SCOPES = "scopes"
    BUSINESSES = "businesses"
    SECRETS = "secrets"
    AUDIT = "audit"
    HANDOFFS = "handoffs"
    AGENT_TOKENS = "agentTokens"
    ASSIGNMENTS = "assignments"
    #: Advertisements karbon-admin writes and publishes to businesses (a copy in each `b-{id}`).
    ADVERTISEMENTS = "advertisements"
    #: Demo-owned: the browser demo's callers, calls and messages, in snake_case.
    CALLERS = "callers"


class BusinessDb(StrEnum):
    """`b-{businessId}`: one database per business."""

    USERS = "users"
    ROLES = "roles"
    BRANCHES = "branches"
    SETTINGS = "settings"
    CHECKLIST = "checklist"
    GRANTS = "grants"
    NUMBERS = "numbers"
    AUDIOS = "audios"
    CALLERS = "callers"
    ORDERS = "orders"
    BOOKINGS = "bookings"
    COMPLAINTS = "complaints"
    MENU = "menu"
    AUDIT = "audit"
    #: Published copies of `(default)/advertisements` for the console; `dismissed` is the business's.
    ADVERTISEMENTS = "advertisements"


class Sub(StrEnum):
    """Subcollections: `callers/{phone}/calls/{id}/messages/{n}`, in both kinds of database."""

    CALLS = "calls"
    MESSAGES = "messages"


Collection = Default | BusinessDb

#: settings/{id} in a business database.
AGENT_SETTINGS = "agent"
CONSOLE_SETTINGS = "console"


# ── doc ids ─────────────────────────────────────────────────────────────────
def assignment_id(serviceperson_uid: str, user_uid: str) -> str:
    return f"{serviceperson_uid}_{user_uid}"


# ── camelCase on disk ───────────────────────────────────────────────────────
def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def to_doc(value: Any) -> Any:
    """A model dump (or any dict) with every field name camelCase, nested ones too."""
    if isinstance(value, dict):
        return {camel(k): to_doc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_doc(v) for v in value]
    return value


#: For models stored in Firestore: reads the camelCase document, and still takes snake_case
#: from the API and the code. Serialising (the API's JSON) is untouched.
STORED = ConfigDict(
    alias_generator=AliasGenerator(validation_alias=lambda name: AliasChoices(name, camel(name))),
    populate_by_name=True,
)
