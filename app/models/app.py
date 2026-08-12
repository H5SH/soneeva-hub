from pydantic import BaseModel, Field, field_validator


class PlanConfig(BaseModel):
    """A purchasable (or free) tier of one app. Price/currency come from Stripe at read time."""

    code: str
    name: str
    description: str | None = None
    price_id: str | None = Field(default=None, description="Stripe price id. None means free tier.")
    trial_days: int | None = None
    amount: int | None = None
    interval: str | None = None
    currency: str | None = None
    highlight: bool = False
    features: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """One BigCommerce app: where to send the merchant back, and what it can sell."""

    id: str
    name: str
    free_plan_code: str | None = None
    plans: list[PlanConfig]

    @field_validator("plans")
    @classmethod
    def _unique_codes(cls, plans: list[PlanConfig]) -> list[PlanConfig]:
        codes = [p.code for p in plans]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate plan codes")
        return plans

    def plan(self, code: str) -> PlanConfig | None:
        return next((p for p in self.plans if p.code == code), None)

    def plan_by_price(self, price_id: str) -> PlanConfig | None:
        return next((p for p in self.plans if p.price_id == price_id), None)

    @property
    def free_plan(self) -> PlanConfig | None:
        return self.plan(self.free_plan_code) if self.free_plan_code else None

    @property
    def paid_plans(self) -> list[PlanConfig]:
        return [p for p in self.plans if p.amount]