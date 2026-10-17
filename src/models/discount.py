"""What a coupon takes off. A 100%-off-forever discount is this with its defaults."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

MAX_PERCENT = 100.0


class DiscountKind(StrEnum):
    PERCENT = "percent"
    AMOUNT = "amount"


class Duration(StrEnum):
    ONCE = "once"
    REPEATING = "repeating"
    FOREVER = "forever"


class Discount(BaseModel):
    kind: DiscountKind = DiscountKind.PERCENT
    value: float = Field(
        default=MAX_PERCENT, description="A percentage for `percent`, minor units for `amount`."
    )
    currency: str = Field(default="usd", description="Only read for an `amount` discount.")
    duration: Duration = Duration.FOREVER
    months: int | None = Field(default=None, ge=1, description="How long `repeating` lasts.")
    max_redemptions: int | None = Field(default=None, ge=1, description="None is unlimited.")
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "Discount":
        """Refused here, before Stripe: its error for a bad combination is accurate but unreadable."""
        if self.value <= 0:
            raise ValueError("A discount has to take something off.")
        if self.kind is DiscountKind.PERCENT and self.value > MAX_PERCENT:
            raise ValueError("A percentage cannot be more than 100.")
        if self.kind is DiscountKind.AMOUNT and self.value != int(self.value):
            raise ValueError("An amount is in minor units, so it must be whole.")
        if self.duration is Duration.REPEATING and not self.months:
            raise ValueError("A repeating discount needs a number of months.")
        if self.duration is not Duration.REPEATING and self.months:
            raise ValueError("Months only apply to a repeating discount.")
        return self

    @classmethod
    def build(cls, **terms: Any) -> "Discount":
        """Like the constructor, but a refusal is a plain ValueError carrying the one readable sentence."""
        try:
            return cls(**terms)
        except ValidationError as error:
            raise ValueError(str(error.errors()[0]["msg"]).removeprefix("Value error, ")) from None

    def describe(self) -> str:
        """What the CLI prints, so every front end words it the same way."""
        off = (
            f"{self.value:g}% off" if self.kind is DiscountKind.PERCENT
            else f"{self.value / 100:,.2f} {self.currency.upper()} off"
        )
        lasts = {Duration.FOREVER: "forever", Duration.ONCE: "once",
                 Duration.REPEATING: f"for {self.months} months"}[self.duration]
        return f"{off}, {lasts}"


class Coupon(BaseModel):
    """A Stripe Promotion Code (metadata app, createdBy) and the Coupon it redeems (metadata app)."""

    code: str
    app_id: str
    coupon_id: str
    promotion_code_id: str
    created_by: str = ""
    discount: Discount = Field(default_factory=Discount)
    times_redeemed: int = 0
    active: bool = True
