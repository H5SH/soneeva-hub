from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SubscriptionStatus(StrEnum):
    """Mirrors the gateway statuses, plus `none` for stores that never subscribed."""

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
    """The shape every layer shares: gateway, storage and API responses all extend this."""

    status: SubscriptionStatus = SubscriptionStatus.NONE
    customer_id: str | None = None
    subscription_id: str | None = None
    price_id: str | None = None
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
    """As returned by the payment gateway, before we attach it to an app and store."""

    metadata: dict[str, str] = Field(default_factory=dict)


class SubscriptionRecord(Subscription):
    """What we persist. The gateway stays the source of truth; this is the read cache."""

    app_id: str
    store_hash: str
    plan_code: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> str:
        return f"{self.app_id}__{self.store_hash}"
