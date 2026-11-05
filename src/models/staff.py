"""/v1/payment, /v1/handoff and console billing: bodies and the camelCase views returned."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.models.app import PlanStatus
from src.models.discount import Discount, DiscountKind, Duration
from src.services.errors import AdminError

APP_ID = r"^[a-z0-9][a-z0-9_-]{1,62}$"


class AppCreate(BaseModel):
    id: str = Field(pattern=APP_ID)
    name: str = Field(default="", max_length=120)
    returnUrl: str = Field(default="", max_length=500)
    allowedReturnHosts: list[str] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=20)


class AppPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    returnUrl: str | None = Field(default=None, max_length=500)
    allowedReturnHosts: list[str] | None = Field(default=None, max_length=50)
    freePlanCode: str | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    webhookSecret: str | None = Field(default=None, max_length=200, description="Write-only; blank keeps it.")
    stagingWebhookSecret: str | None = Field(default=None, max_length=200, description="The same, test mode.")

    def fields(self) -> dict[str, Any]:
        """Stored (snake_case) names; an empty returnUrl or freePlanCode clears it, a blank webhook
        secret leaves it (it is never read back, so a plain save must not wipe it)."""
        names = {"name": "name", "returnUrl": "return_url", "allowedReturnHosts": "allowed_return_hosts",
                 "freePlanCode": "free_plan_code", "tags": "tags"}
        given = self.model_dump(exclude_unset=True)
        secrets = {"stripe_webhook_secret": (given.pop("webhookSecret", None) or "").strip(),
                   "staging_webhook_secret": (given.pop("stagingWebhookSecret", None) or "").strip()}
        return {names[k]: v or None for k, v in given.items() if k in ("returnUrl", "freePlanCode")} | {
            names[k]: v for k, v in given.items()
            if k in ("name", "allowedReturnHosts", "tags") and v is not None
        } | {k: v for k, v in secrets.items() if v}


class PlanPut(BaseModel):
    """Amount is minor units per unit; 0 is the free tier. Whatever is null keeps what is saved, or takes
    the default for a new plan. A different amount, unit or interval makes a new Stripe price."""

    name: str = Field(default="", max_length=120)
    description: str | None = Field(default=None, max_length=500)
    amount: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[a-z]{3}$")
    interval: str | None = Field(default=None, pattern=r"^(day|week|month|year)$")
    unit: str | None = None
    trialDays: int | None = Field(default=None, ge=0, le=730)
    features: list[str] | None = Field(default=None, max_length=50)
    status: PlanStatus | None = None
    contact: bool | None = None


class PlanCreate(PlanPut):
    """POST: the code is derived from the name ("Pro Plus" -> pro-plus) unless given."""

    code: str = Field(default="", max_length=60, pattern=r"^[a-z0-9-]*$")


class PlanStatusPut(BaseModel):
    status: PlanStatus


class CouponCreate(BaseModel):
    """`value` is a percentage for `percent`, minor units for `amount`. Defaults: 100% off forever."""

    code: str = Field(default="", max_length=40)
    kind: DiscountKind = DiscountKind.PERCENT
    value: float = 100
    currency: str = Field(default="usd", pattern=r"^[a-z]{3}$")
    duration: Duration = Duration.FOREVER
    months: int | None = Field(default=None, ge=1, le=120)
    maxRedemptions: int | None = Field(default=None, ge=1)
    expiresAt: datetime | None = None

    def terms(self) -> Discount:
        """Incoherent terms are refused with a readable sentence, before anything reaches Stripe."""
        try:
            return Discount.build(
                kind=self.kind, value=self.value, currency=self.currency, duration=self.duration,
                months=self.months, max_redemptions=self.maxRedemptions, expires_at=self.expiresAt,
            )
        except ValueError as error:
            raise AdminError(str(error)) from error


class HandoffRequest(BaseModel):
    businessId: str = Field(min_length=1, max_length=64)


class HandoffRedeem(BaseModel):
    code: str = Field(min_length=20, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class CheckoutBody(BaseModel):
    plan: str = Field(min_length=1, max_length=60)
    successUrl: str = Field(max_length=1000)
    cancelUrl: str = Field(max_length=1000)


class PortalBody(BaseModel):
    returnUrl: str = Field(max_length=1000)


