from abc import ABC, abstractmethod
from typing import Any

from src.models.people import Account


class AccountError(Exception):
    """An account call failed. `code`: email-exists | not-found | invalid-input | unavailable."""

    STATUS = {"email-exists": 409, "not-found": 404, "invalid-input": 422, "unavailable": 503}

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def status(self) -> int:
        return self.STATUS.get(self.code, 400)


class AccountProvider(ABC):
    """Sign-in accounts. Only karbon-hub holds the admin rights behind this."""

    @abstractmethod
    async def page(self, token: str | None) -> tuple[list[Account], str | None]:
        """One page of every account, and the token for the next (None at the end)."""

    @abstractmethod
    async def get(self, uid: str) -> Account | None: ...

    @abstractmethod
    async def create(self, email: str, password: str, display_name: str, claims: dict[str, Any]) -> Account:
        """Raises AccountError("email-exists") when the address is taken."""

    @abstractmethod
    async def update(self, uid: str, fields: dict[str, Any]) -> Account:
        """Any of email, password, displayName, disabled."""

    @abstractmethod
    async def set_claims(self, uid: str, claims: dict[str, Any]) -> None:
        """Merge into the custom claims; a None value removes that claim."""

    @abstractmethod
    async def delete(self, uid: str) -> None:
        """Gone already counts as deleted."""

    @abstractmethod
    async def reset_link(self, email: str) -> str: ...

    @abstractmethod
    async def send_reset(self, email: str) -> None:
        """Firebase emails the link with its own template."""
