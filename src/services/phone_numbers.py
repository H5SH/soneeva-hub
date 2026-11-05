"""Twilio numbers for a business, only while it is on a plan."""

from datetime import UTC, datetime

from src.db import Database
from src.models.db import Default
from src.models.platform import Business, PhoneNumber
from src.numbers import AvailableNumber, NumberProvider, NumbersError
from src.services.audit import audit
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.voice import STATUS_PATH, WEBHOOK_PATH, VoiceService
from src.tunables import tunable


class PhoneNumberService:
    def __init__(self, db: Database, numbers: NumberProvider, businesses: BusinessService,
                 voice: VoiceService) -> None:
        self._db = db
        self._numbers = numbers
        self._businesses = businesses
        self._voice = voice

    async def _require_plan(self, business: Business) -> None:
        record = await self._businesses.record(business)
        statuses = tunable("numbers.requireStatuses")
        if record.status not in statuses or not self._businesses.serving(business, record):
            raise PlatformError("A number needs an active plan", 402)

    async def search(self, business: Business, country: str, contains: str) -> list[AvailableNumber]:
        await self._require_plan(business)
        try:
            return await self._numbers.search(
                country, contains, tunable("numbers.type"), tunable("numbers.searchLimit")
            )
        except NumbersError as error:
            raise PlatformError(str(error), 502) from error

    async def _subaccount(self, business: Business) -> str:
        """Made once: kept before anything else, so a failure below reuses it on the next purchase rather
        than paying for another, and named on the business only once its secret has it."""
        kept = await self._db.get_row(Default.SECRETS, business.id) or {}
        sid, token = kept.get("twilioAccountSid"), kept.get("twilioAuthToken")
        if not sid:
            made = await self._numbers.create_subaccount(f"{business.name} ({business.id})")
            sid, token = made.sid, made.token
            await self._db.upsert_live(Default.SECRETS, business.id,
                                   {"twilioAccountSid": sid, "twilioAuthToken": token})
        await self._voice.add(business.id, {"TWILIO_ACCOUNT_SID": sid, "TWILIO_AUTH_TOKEN": token})
        await self._businesses.set_fields(business.id, {"twilioAccountSid": sid})
        return sid

    async def buy(self, business: Business, phone: str, by: str) -> PhoneNumber:
        """On the voice's URL. The first one makes the business's Twilio subaccount, which goes into its
        voice secret: the developer's next voice revision picks it up."""
        await self._require_plan(business)
        if not business.voiceUrl:
            raise PlatformError("The voice service is not deployed yet: its URL is not set", 409)
        try:
            account = business.twilioAccountSid or await self._subaccount(business)
            base = business.voiceUrl.rstrip("/")
            # An answer lost after Twilio bought it must not leave a paid number unrecorded, or buy it twice.
            sid = await self._numbers.owned(account, phone) or await self._numbers.buy(
                account, phone, base + WEBHOOK_PATH, base + STATUS_PATH)
        except NumbersError as error:
            raise PlatformError(str(error), 502) from error
        number = PhoneNumber(phoneNumber=phone, sid=sid, at=datetime.now(UTC).isoformat())
        await self._businesses.add_number(business.id, number)
        await audit(self._db, "number.buy", business.id, by, {"phone": phone})
        return number
