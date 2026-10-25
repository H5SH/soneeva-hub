from abc import ABC, abstractmethod

from pydantic import BaseModel


class MenuRow(BaseModel):
    """One item as the console's menu takes it (console lib/business/menu-csv.ts)."""

    name: str
    category: str
    price: float
    description: str = ""
    available: bool = True


class Reader(ABC):
    """Reads a photo or PDF of a business document (a menu, a price list, a flyer). Nothing is stored."""

    @abstractmethod
    async def text(self, data: bytes, kind: str) -> str:
        """Everything a phone agent needs from it, as plain text."""

    @abstractmethod
    async def menu(self, data: bytes, kind: str) -> list[MenuRow]:
        """Its items, one row each."""
