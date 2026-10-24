from src.vault.base import Vault


class FakeVault(Vault):
    """In memory, for tests: `versions[name]` is every value written, oldest first."""

    def __init__(self) -> None:
        self.versions: dict[str, list[str]] = {}

    async def read(self, name: str) -> str | None:
        return (self.versions.get(name) or [None])[-1]

    async def write(self, name: str, value: str) -> None:
        self.versions.setdefault(name, []).append(value)
