"""What the internal app shows per business: detail lines from Stripe, never totals or trends."""

from typing import Any

from src.models.admin import Principal
from src.models.payment import Invoice
from src.models.platform import Business
from src.models.subscription import SubscriptionRecord
from src.payment import PaymentGateway
from src.services.billing import BillingService
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.modes import ByMode
from src.services.usage import UsageService
from src.tunables import tunable


class CustomerService:
    def __init__(self, payment: ByMode[PaymentGateway], billing: ByMode[BillingService],
                 businesses: BusinessService, usage: UsageService) -> None:
        self._payment = payment
        self._billing = billing
        self._businesses = businesses
        self._usage = usage

    async def every(self, person: Principal) -> list[tuple[Business, SubscriptionRecord]]:
        shown = [b for b in await self._businesses.every() if person.reaches(b.id, b.appId)]
        records = await self._businesses.subscriptions(shown)
        return [(b, records.get(b.id) or SubscriptionRecord(app_id=b.appId, tenant=b.id)) for b in shown]

    async def one(self, person: Principal, business_id: str) -> tuple[Business, SubscriptionRecord]:
        business = await self._businesses.get(business_id)
        if not business or not person.reaches(business.id, business.appId):
            raise PlatformError("No such business", 404)
        return business, await self._billing[business.mode].get_record(business.appId, business.id)

    async def invoices(self, business: Business, record: SubscriptionRecord) -> list[Invoice]:
        if not record.customer_id:
            return []
        try:
            shown = tunable("customers.invoicesShown")
            found, _ = await self._payment[business.mode].list_invoices(record.customer_id, shown)
        except Exception as error:  # noqa: BLE001 - Stripe's message is what the operator needs
            raise PlatformError(f"Stripe: {error}", 502) from error
        return found

    async def usage(self, business: Business, record: SubscriptionRecord) -> dict[str, Any]:
        return {"days": await self._usage.days(business, record)}
