from abc import ABC, abstractmethod
from decimal import Decimal


class StoreBillingError(Exception):
    """The store refused the charge or did not answer; the voice reports the call again."""


class StoreBilling(ABC):
    """Charges a business that pays through its store."""

    @abstractmethod
    async def charge(self, store: str, sealed_token: str, line_item: str, amount: Decimal, currency: str,
                     description: str, key: str) -> None:
        """One usage charge. The same `key` doesn't charge twice for 24 hours, Shopify's limit: a call
        reported later than that is charged again."""
