from abc import ABC, abstractmethod


class Vault(ABC):
    """Named secrets, one text value each; a write adds a new version."""

    @abstractmethod
    async def read(self, name: str) -> str | None:
        """The latest version, or None when there is none."""

    @abstractmethod
    async def write(self, name: str, value: str) -> None:
        """Creates the secret when missing, then adds `value` as its latest version."""
