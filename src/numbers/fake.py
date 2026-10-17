from src.numbers.base import AvailableNumber, NumberProvider, Subaccount


class FakeNumbers(NumberProvider):
    """In memory, for tests and offline runs. Records what was bought."""

    def __init__(self) -> None:
        self.subaccounts: list[str] = []
        self.bought: list[tuple[str, str, str]] = []

    async def search(self, country: str, contains: str, kind: str, limit: int) -> list[AvailableNumber]:
        return [AvailableNumber(phoneNumber=f"+3120{contains or '0'}{n:04d}", country=country.upper())
                for n in range(min(limit, 3))]

    async def create_subaccount(self, name: str) -> Subaccount:
        self.subaccounts.append(name)
        return Subaccount(sid=f"AC{len(self.subaccounts):032d}", token="fake-token")

    async def buy(self, account_sid: str, phone: str, voice_url: str, status_url: str) -> str:
        self.bought.append((account_sid, phone, voice_url))
        return f"PN{len(self.bought):032d}"
