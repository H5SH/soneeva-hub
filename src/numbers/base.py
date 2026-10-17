from abc import ABC, abstractmethod

from pydantic import BaseModel


class NumbersError(Exception):
    """The telephony provider refused."""


class AvailableNumber(BaseModel):
    phoneNumber: str
    friendlyName: str = ""
    locality: str = ""
    region: str = ""
    country: str = ""


class Subaccount(BaseModel):
    sid: str
    token: str


class NumberProvider(ABC):
    """Phone numbers, bought on one subaccount per business."""

    @abstractmethod
    async def search(self, country: str, contains: str, kind: str, limit: int) -> list[AvailableNumber]: ...

    @abstractmethod
    async def create_subaccount(self, name: str) -> Subaccount: ...

    @abstractmethod
    async def buy(self, account_sid: str, phone: str, voice_url: str, status_url: str) -> str:
        """Purchase onto the subaccount with its voice webhook set. Returns the number's sid."""
