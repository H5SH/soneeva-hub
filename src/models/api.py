from datetime import datetime

from pydantic import BaseModel, Field

from src.models.app import Plan
from src.models.payment import Invoice
from src.models.subscription import SubscriptionRecord, SubscriptionStatus


class PlanResponse(Plan):
    is_current: bool = False


class SubscriptionResponse(SubscriptionRecord):
    active: bool = False
    plan_name: str | None = None


class EntitlementsResponse(BaseModel):
    """Cheap, cacheable gate check. This is what each app polls."""

    app_id: str
    tenant: str
    plan_code: str | None = None
    status: SubscriptionStatus
    active: bool
    in_grace_period: bool = Field(default=False, description="Payment failed but not yet canceled")
    trialing: bool = False
    features: list[str] = []
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False


class InvoiceListResponse(BaseModel):
    data: list[Invoice]
    has_more: bool = False


class CheckoutRequest(BaseModel):
    plan_code: str
    success_url: str | None = Field(default=None, description="Falls back to the app's return_url")
    cancel_url: str | None = None
    promo_code: str | None = None
    quantity: int = Field(default=1, ge=1)
    trial_days: int | None = Field(default=None, ge=0, le=730)


class PortalRequest(BaseModel):
    return_url: str | None = None


class ChangePlanRequest(BaseModel):
    plan_code: str
    proration_behavior: str = Field(
        default="create_prorations", pattern="^(create_prorations|none|always_invoice)$"
    )


class CancelRequest(BaseModel):
    at_period_end: bool = True


class UrlResponse(BaseModel):
    url: str


class HealthResponse(BaseModel):
    """Deliberately thin: this endpoint is public."""

    status: str
