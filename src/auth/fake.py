from typing import Any

from src.auth.base import AccountError, AccountProvider
from src.models.people import Account


class FakeAccounts(AccountProvider):
    """In memory, for tests. `claims` and `passwords` are kept for assertions."""

    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.passwords: dict[str, str] = {}
        self.sent: list[str] = []
        self.fail_claims = False

    def add(self, uid: str, email: str = "", **claims: Any) -> Account:
        self.accounts[uid] = Account(uid=uid, email=email or f"{uid}@example.com", displayName=uid)
        self.claims[uid] = {}
        self._apply(uid, claims)
        return self.accounts[uid]

    def _apply(self, uid: str, claims: dict[str, Any]) -> None:
        merged = {k: v for k, v in (self.claims.get(uid, {}) | claims).items() if v is not None}
        self.claims[uid] = merged
        self.accounts[uid] = self.accounts[uid].model_copy(update={
            "username": merged.get("username"), "businessId": merged.get("businessId") or None,
        })

    async def page(self, token: str | None) -> tuple[list[Account], str | None]:
        return list(self.accounts.values()), None

    async def get(self, uid: str) -> Account | None:
        return self.accounts.get(uid)

    async def create(self, email: str, password: str, display_name: str, claims: dict[str, Any]) -> Account:
        if any(a.email == email for a in self.accounts.values()):
            raise AccountError("email-exists", "That email already has an account")
        uid = f"uid-{len(self.accounts) + 1}"
        self.accounts[uid] = Account(uid=uid, email=email, displayName=display_name)
        self.passwords[uid] = password
        if self.fail_claims:
            raise AccountError("unavailable", "claims refused")
        self._apply(uid, claims)
        return self.accounts[uid]

    async def update(self, uid: str, fields: dict[str, Any]) -> Account:
        if uid not in self.accounts:
            raise AccountError("not-found", "No such account")
        if fields.get("password"):
            self.passwords[uid] = fields["password"]
        kept = ("email", "displayName", "disabled")
        changes = {k: v for k, v in fields.items() if k in kept and v is not None}
        self.accounts[uid] = self.accounts[uid].model_copy(update=changes)
        return self.accounts[uid]

    async def set_claims(self, uid: str, claims: dict[str, Any]) -> None:
        if uid not in self.accounts:
            raise AccountError("not-found", "No such account")
        self._apply(uid, claims)

    async def delete(self, uid: str) -> None:
        self.accounts.pop(uid, None)

    async def reset_link(self, email: str) -> str:
        return f"https://reset.example/{email}"

    async def send_reset(self, email: str) -> None:
        self.sent.append(email)
