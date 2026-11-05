from decimal import Decimal

from src.stores.base import StoreBilling


class FakeStoreBilling(StoreBilling):
    """In memory, for tests: `charges[key]` is each charge, so a repeated key charges once."""

    def __init__(self) -> None:
        self.charges: dict[str, tuple[str, str, str, Decimal, str]] = {}

    async def charge(self, store: str, sealed_token: str, line_item: str, amount: Decimal, currency: str,
                     description: str, key: str) -> None:
        self.charges[key] = (store, sealed_token, line_item, amount, currency)
