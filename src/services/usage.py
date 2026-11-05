"""Call seconds go straight to the subscription's Stripe meter, or to Shopify for a business that pays
through it; the call id makes a retry a no-op."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.models.app import UNIT_SECONDS, Plan
from src.models.db import BusinessDb
from src.models.platform import Business, UsageReport
from src.models.subscription import SubscriptionRecord
from src.payment import PaymentGateway
from src.services.billing import BillingService
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.modes import ByMode
from src.services.plans import PlanService
from src.stores import StoreBilling, StoreBillingError
from src.tunables import tunable


def usage_cents(plan: Plan, seconds: int) -> int:
    """The plan's price for these seconds, up to the cent; 0 for a plan that isn't metered."""
    if not plan.metered or not plan.amount:
        return 0
    return -(-plan.amount * seconds // UNIT_SECONDS[plan.unit])


class UsageService:
    def __init__(self, payment: ByMode[PaymentGateway], billing: ByMode[BillingService],
                 businesses: BusinessService, plans: ByMode[PlanService],
                 store_billing: StoreBilling) -> None:
        self._payment = payment
        self._billing = billing
        self._businesses = businesses
        self._plans = plans
        self._store_billing = store_billing

    async def record(self, business_id: str, report: UsageReport) -> dict[str, Any]:
        """Billed only on an active metered plan; without one the call is not billable."""
        if report.seconds > tunable("usage.maxSeconds"):
            raise PlatformError("seconds is longer than any call", 422)
        business = await self._businesses.require(None, business_id)
        if business.storePlan:
            return {"status": "recorded", "billed": await self._charge_store(business, report)}
        record = await self._billing[business.mode].get_record(business.appId, business_id)
        if not (record.is_active and record.customer_id and record.meter_id):
            return {"status": "recorded", "billed": False}
        new = await self._payment[business.mode].record_usage(
            record.meter_id, record.customer_id, report.seconds, report.callId)
        return {"status": "recorded" if new else "duplicate", "billed": True}

    async def _charge_store(self, business: Business, report: UsageReport) -> bool:
        """Whether it was charged. A failure is a 502, so the voice reports the call again."""
        plan = business.storePlan
        priced = await self._plans[business.mode].get(business.appId, plan.code) if plan else None
        cents = usage_cents(priced, report.seconds) if priced else 0
        if not (plan and priced and plan.is_active and plan.usageLineItemId and cents):
            return False
        bdb = self._businesses.business_db(business)
        connection = await bdb.get_row(BusinessDb.CONNECTIONS, plan.source)
        try:
            if not connection:
                raise StoreBillingError("The business has no store token to charge with")
            await self._store_billing.charge(
                connection["storeId"], connection["token"], plan.usageLineItemId, Decimal(cents) / 100,
                priced.currency.upper(), f"{report.seconds} seconds of calls", report.callId)
        except StoreBillingError as error:
            if not business.chargeFailedAt:
                await self._businesses.set_fields(business.id, {"chargeFailedAt": datetime.now(UTC)})
            raise PlatformError(str(error), 502) from error
        if business.chargeFailedAt:
            await self._businesses.set_fields(business.id, {"chargeFailedAt": None})
        return True

    async def days(self, business: Business, record: SubscriptionRecord) -> list[dict[str, Any]]:
        """Seconds per UTC day from the meter's event summaries, newest first."""
        if not (record.meter_id and record.customer_id):
            return []
        try:
            return await self._payment[business.mode].usage_days(
                record.meter_id, record.customer_id, tunable("customers.usageDaysShown"))
        except Exception as error:  # noqa: BLE001 - Stripe's message is what the operator needs
            raise PlatformError(f"Stripe: {error}", 502) from error
