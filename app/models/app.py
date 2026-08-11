from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl, field_validator


class PlanConfig(BaseModel):
    """A purchasable (or free) tier of one app. Price/currency come from Stripe at read time."""

    code: str
    name: str
    description: str | None = None
    price_id: str | None = Field(default=None, description="Stripe price id. None means free tier.")
    trial_days: int | None = None
    highlight: bool = False
    features: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)

    @property
    def is_free(self) -> bool:
        return self.price_id is None


class AppConfig(BaseModel):
    """One BigCommerce app: where to send the merchant back, and what it can sell."""

    app_id: str
    name: str
    return_url: HttpUrl
    cancel_url: HttpUrl | None = None
    allowed_return_hosts: list[str] = Field(default_factory=list)
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
        return [p for p in self.plans if not p.is_free]

    def resolve_return_url(self, requested: str | None) -> str:
        """Only honour a caller-supplied redirect if it points at a host this app declared."""
        if not requested:
            return str(self.return_url)
        host = urlparse(requested).hostname or ""
        allowed = set(self.allowed_return_hosts)
        for configured in (self.return_url, self.cancel_url):
            if configured and (h := urlparse(str(configured)).hostname):
                allowed.add(h)
        return requested if host in allowed else str(self.return_url)
