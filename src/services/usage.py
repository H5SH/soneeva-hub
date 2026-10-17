"""Call seconds go straight to the subscription's Stripe meter; the call id makes a retry a no-op."""

from typing import Any

from src.models.platform import Business, UsageReport
from src.models.subscription import SubscriptionRecord
from src.payment import PaymentGateway
from src.services.billing import BillingService
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.modes import ByMode
from src.tunables import tunable


class UsageService:
    def __init__(self, payment: ByMode[PaymentGateway], billing: ByMode[BillingService],
                 businesses: BusinessService) -> None:
        self._payment = payment
        self._billing = billing
        self._businesses = businesses

    async def record(self, business_id: str, report: UsageReport) -> dict[str, Any]:
        """Billed only on an active metered plan; without one the call is not billable."""
        if report.seconds > tunable("usage.maxSeconds"):
            raise PlatformError("seconds is longer than any call", 422)
        business = await self._businesses.require(None, business_id)
        record = await self._billing[business.mode].get_record(business.appId, business_id)
        if not (record.is_active and record.customer_id and record.meter_id):
            return {"status": "recorded", "billed": False}
        new = await self._payment[business.mode].record_usage(
            record.meter_id, record.customer_id, report.seconds, report.callId)
        return {"status": "recorded" if new else "duplicate", "billed": True}

    async def days(self, business: Business, record: SubscriptionRecord) -> list[dict[str, Any]]:
        """Seconds per UTC day from the meter's event summaries, newest first."""
        if not (record.meter_id and record.customer_id):
            return []
        try:
            return await self._payment[business.mode].usage_days(
                record.meter_id, record.customer_id, tunable("customers.usageDaysShown"))
        except Exception as error:  # noqa: BLE001 - Stripe's message is what the operator needs
            raise PlatformError(f"Stripe: {error}", 502) from error
