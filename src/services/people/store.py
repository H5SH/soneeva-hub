"""What `(default)` holds about platform people: `users/{uid}`, `roles/{id}`, `assignments/{id}`.

Raw documents rather than rows: the console created them without the `isDeleted` flag.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from src.db import DELETE, Database, Increment, Query, Transaction, Write
from src.models.admin import person_scopes
from src.models.db import DELETED_AT, DELETED_FLAG, UPDATED_AT, Default, assignment_id

#: (current tokens of the target, giver's balance) -> how much the giver's balance moves.
TokenPlan = Callable[[int | None, int | None], int]


def read_tokens(data: dict[str, Any] | None) -> int | None:
    """Missing is no limit; present but unreadable is spent, as the call assistant reads it."""
    if not data or "tokens" not in data:
        return None
    try:
        return int(data["tokens"])
    except (TypeError, ValueError):
        return 0


def _strings(value: Any) -> list[str]:
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def _profile(data: dict[str, Any]) -> dict[str, Any]:
    role, phone = data.get("role"), data.get("phone")
    return {"role": role if isinstance(role, str) and role else None, "tokens": read_tokens(data),
            "phone": phone if isinstance(phone, str) else "",
            "disabled": data.get("disabled") is True, "apps": _strings(data.get("apps")),
            "businesses": _strings(data.get("businesses"))}


class People:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def profile(self, uid: str) -> dict[str, Any] | None:
        """`users/{uid}`: role, tokens (None = no limit), disabled, apps, businesses; None without a doc."""
        data = await self._db.get(Default.USERS, uid)
        return _profile(data) if data is not None else None

    async def profiles(self, uids: list[str]) -> dict[str, dict[str, Any]]:
        return {uid: _profile(d) for uid, d in (await self._db.get_all(Default.USERS, uids)).items()}

    async def with_role(self, role: str) -> dict[str, dict[str, Any]]:
        """Every `users/{uid}` holding this role, by uid."""
        docs = await self._db.query(Query(Default.USERS).where("role", "==", role))
        return {d.id: _profile(d.data) for d in docs}

    async def save(self, uid: str, fields: dict[str, Any]) -> None:
        """Merge role, apps, businesses, disabled, email, phone into `users/{uid}`; a None role drops it."""
        if "role" in fields:
            fields = fields | {"role": fields["role"] or DELETE}
        await self._db.set(Default.USERS, uid, fields | {UPDATED_AT: datetime.now(UTC)})

    async def remove(self, uid: str) -> None:
        await self._db.batch([Write(Default.USERS, uid)])

    async def transfer(self, target: str, tokens: int | None, giver: str | None, plan: TokenPlan) -> None:
        """Set `target`'s tokens and move `plan(...)` into the giver's, in one transaction.
        `plan` raises to refuse; it sees values read inside the transaction."""

        async def run(tx: Transaction) -> None:
            current = read_tokens(await tx.get(Default.USERS, target))
            balance = read_tokens(await tx.get(Default.USERS, giver)) if giver else None
            delta = plan(current, balance)
            tx.set(Default.USERS, target, {"tokens": DELETE if tokens is None else tokens})
            if giver and delta:
                tx.set(Default.USERS, giver, {"tokens": Increment(delta)})

        await self._db.transaction(run)

    async def add_tokens(self, uid: str, amount: int) -> None:
        if amount:
            await self._db.set(Default.USERS, uid, {"tokens": Increment(amount)})

    async def assignments(self) -> list[tuple[str, str]]:
        """Every (servicepersonUid, userUid)."""
        found: list[tuple[str, str]] = []
        for doc in await self._db.query(Query(Default.ASSIGNMENTS)):
            sp, user = doc.data.get("servicepersonUid"), doc.data.get("userUid")
            if isinstance(sp, str) and isinstance(user, str) and not doc.data.get(DELETED_FLAG):
                found.append((sp, user))
        return found

    async def set_assignment(self, user: str, servicepersons: list[str]) -> None:
        current = {sp for sp, u in await self.assignments() if u == user}
        wanted, now = set(servicepersons), datetime.now(UTC)
        await self._db.batch(
            [Write(Default.ASSIGNMENTS, assignment_id(sp, user)) for sp in current - wanted]
            + [Write(Default.ASSIGNMENTS, assignment_id(sp, user),
                     {"servicepersonUid": sp, "userUid": user, "createdAt": now}) for sp in wanted - current])

    async def revoke_all(self, uid: str) -> int:
        """Drop every assignment held by or over `uid`; returns how many went."""
        theirs = [(sp, u) for sp, u in await self.assignments() if uid in (sp, u)]
        if theirs:
            await self._db.batch([Write(Default.ASSIGNMENTS, assignment_id(sp, u)) for sp, u in theirs])
        return len(theirs)

    async def roles(self) -> dict[str, list[str]]:
        """`roles/{id}` → person scopes: console ids (`*` as stored), `payment.*`, `platform.*`."""
        docs = await self._db.query(Query(Default.ROLES))
        return {d.id: person_scopes(d.data.get("scopes")) for d in docs if not d.data.get(DELETED_FLAG)}

    async def save_roles(self, roles: dict[str, list[str] | None]) -> None:
        """Replace each role's scopes; None removes the role."""
        now = datetime.now(UTC)
        gone = {DELETED_FLAG: True, DELETED_AT: now}
        await self._db.batch([
            Write(Default.ROLES, name, gone if scopes is None
                  else {"scopes": scopes, DELETED_FLAG: False, UPDATED_AT: now}, merge=True)
            for name, scopes in roles.items()])

    async def holders(self, role: str) -> int:
        """How many `users/{uid}` docs carry this role."""
        return await self._db.count(Query(Default.USERS).where("role", "==", role))
