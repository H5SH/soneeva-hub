from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SubscriptionStatus(StrEnum):
    """Mirrors the gateway statuses, plus `none` for tenants that never subscribed."""

    NONE = "none"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAUSED = "paused"

    @property
    def grants_access(self) -> bool:
        return self in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)


class Subscription(BaseModel):
    """The shape the gateway and the API share. Nothing of it is stored: Stripe holds it."""

    status: SubscriptionStatus = SubscriptionStatus.NONE
    customer_id: str | None = None
    subscription_id: str | None = None
    price_id: str | None = None
    meter_id: str | None = None
    plan_code: str | None = Field(default=None, description="The price's metadata.code.")
    quantity: int = 1
    amount: int | None = Field(default=None, description="Minor units, e.g. 1900 = $19.00")
    currency: str | None = None
    interval: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    trial_end: datetime | None = None
    cancel_at_period_end: bool = False
    canceled_at: datetime | None = None
    latest_invoice_id: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status.grants_access


class GatewaySubscription(Subscription):
    """As the gateway returns it; `metadata` carries {app, tenant}."""

    created: int = 0
    metadata: dict[str, str] = Field(default_factory=dict)


class SubscriptionRecord(Subscription):
    """One tenant of one app, read from Stripe through BillingService's short cache."""

    app_id: str
    tenant: str
