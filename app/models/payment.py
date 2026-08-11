from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Price(BaseModel):
    price_id: str
    amount: int | None = Field(default=None, description="Minor units, e.g. 1900 = $19.00")
    currency: str | None = None
    interval: str | None = None
    interval_count: int | None = None
    trial_days: int | None = None


class CheckoutSession(BaseModel):
    id: str
    url: str | None = None
    customer_id: str | None = None
    subscription_id: str | None = None
    status: str | None = None
    payment_status: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class Invoice(BaseModel):
    id: str
    number: str | None = None
    status: str | None = None
    amount_due: int = 0
    amount_paid: int = 0
    currency: str | None = None
    created: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    hosted_invoice_url: str | None = None
    invoice_pdf: str | None = None


class PaymentMethod(BaseModel):
    id: str | None = None
    brand: str | None = None
    last4: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    wallet: str | None = None


class WebhookEvent(BaseModel):
    id: str
    type: str
    created: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)
