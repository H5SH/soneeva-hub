from src.reader.base import MenuRow, Reader


class FakeReader(Reader):
    """For tests: `read` is every (kind, size) it was given."""

    def __init__(self) -> None:
        self.read: list[tuple[str, int]] = []

    async def text(self, data: bytes, kind: str) -> str:
        self.read.append((kind, len(data)))
        return "Margherita: 9.50"

    async def menu(self, data: bytes, kind: str) -> list[MenuRow]:
        self.read.append((kind, len(data)))
        return [MenuRow(name="Margherita", category="Pizza", price=9.5)]
