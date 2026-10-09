from abc import ABC, abstractmethod

from app.models.admin import Principal


class AuthError(Exception):
    """Sign-in failed, or a session is absent, expired or revoked."""


class Authenticator(ABC):
    """Human sign-in for the dashboard.

    Separate from the Ed25519 app tokens in app/auth/, which authenticate machines. These two
    never meet: a person cannot mint an app token, and an app token cannot reach the UI.
    """

    @abstractmethod
    async def session_from_login(self, id_token: str, ttl_days: int) -> str:
        """Exchange a sign-in for a session token other apps can verify. Raises AuthError."""

    @abstractmethod
    async def principal(self, session_token: str) -> Principal:
        """Verify a session token and resolve the signed-in user. Raises AuthError."""

    @abstractmethod
    async def revoke(self, uid: str) -> None:
        """Invalidate every session for a user."""

    @abstractmethod
    async def create_user(self, email: str) -> tuple[str, str]:
        """Create an account. Returns (uid, password-set link). Raises AuthError."""

    @abstractmethod
    async def delete_user(self, uid: str) -> None:
        """Remove the account entirely. Raises AuthError."""
