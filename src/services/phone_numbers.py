"""Twilio numbers for a business, only while it is on a plan."""

from datetime import UTC, datetime

from src.db import Database
from src.models.db import Default
from src.models.platform import Business, PhoneNumber
from src.numbers import AvailableNumber, NumberProvider, NumbersError
from src.services.billing import BillingService
from src.services.businesses import BusinessService, audit
from src.services.errors import PlatformError
from src.services.modes import ByMode
from src.services.voice import STATUS_PATH, WEBHOOK_PATH, VoiceService
from src.tunables import tunable


class PhoneNumberService:
    def __init__(self, db: Database, numbers: NumberProvider, billing: ByMode[BillingService],
                 businesses: BusinessService, voice: VoiceService) -> None:
        self._db = db
        self._numbers = numbers
        self._billing = billing
        self._businesses = businesses
        self._voice = voice

    async def _require_plan(self, business: Business) -> None:
        record = await self._billing[business.mode].get_record(business.appId, business.id)
        if record.status not in tunable("numbers.requireStatuses"):
            raise PlatformError("A number needs an active plan", 402)

    async def search(self, business: Business, country: str, contains: str) -> list[AvailableNumber]:
        await self._require_plan(business)
        try:
            return await self._numbers.search(
                country, contains, tunable("numbers.type"), tunable("numbers.searchLimit")
            )
        except NumbersError as error:
            raise PlatformError(str(error), 502) from error

    async def buy(self, business: Business, phone: str, by: str) -> PhoneNumber:
        """On the voice's URL. The first one makes the business's Twilio subaccount, which goes into its
        voice secret: the developer's next voice revision picks it up."""
        await self._require_plan(business)
        if not business.voiceUrl:
            raise PlatformError("The voice service is not deployed yet: its URL is not set", 409)
        try:
            account = business.twilioAccountSid
            if not account:
                sub = await self._numbers.create_subaccount(f"{business.name} ({business.id})")
                account = sub.sid
                await self._db.set_row(Default.SECRETS, business.id,
                                       {"twilioAccountSid": sub.sid, "twilioAuthToken": sub.token})
                await self._businesses.set_fields(business.id, {"twilioAccountSid": sub.sid})
                await self._voice.add(business.id, {"TWILIO_ACCOUNT_SID": sub.sid,
                                                    "TWILIO_AUTH_TOKEN": sub.token})
            base = business.voiceUrl.rstrip("/")
            sid = await self._numbers.buy(account, phone, base + WEBHOOK_PATH,
                                          base + STATUS_PATH)
        except NumbersError as error:
            raise PlatformError(str(error), 502) from error
        number = PhoneNumber(phoneNumber=phone, sid=sid, at=datetime.now(UTC).isoformat())
        numbers = [n.model_dump() for n in [*business.numbers, number]]
        await self._businesses.set_fields(business.id, {"numbers": numbers})
        await audit(self._db, "number.buy", business.id, by, {"phone": phone})
        return number
