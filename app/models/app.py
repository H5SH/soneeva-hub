from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class PlanConfig(BaseModel):
    """A purchasable (or free) tier of one app. Price/currency come from Stripe at read time."""

    code: str
    name: str
    description: str | None = None
    price_id: str | None = Field(default=None, description="Stripe price id. None means free tier.")
    product_id: str | None = Field(
        default=None, description="Stripe product. Lets a coupon be restricted to one app."
    )
    trial_days: int | None = None
    amount: int | None = None
    interval: str | None = None
    currency: str | None = None
    highlight: bool = False
    features: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """One calling app: where to send the merchant back, and what it can sell."""

    id: str
    name: str
    free_plan_code: str | None = None
    return_url: str | None = Field(
        default=None, description="Fallback landing page when the caller supplies none."
    )
    allowed_return_hosts: list[str] = Field(
        default_factory=list,
        description="Hosts a redirect may point at. Empty means only return_url is allowed.",
    )
    plans: list[PlanConfig]

    def resolve_return_url(self, candidate: str | None) -> str:
        """Validate a caller-supplied redirect, or fall back to the configured one.

        `/return` and `/cancel` are reachable without a token — the gateway sends the browser
        there — so an unchecked `redirect` would make this service an open redirector for
        anyone who knows an app id. Anything not explicitly allow-listed is discarded rather
        than rejected, so a bad value degrades to the app's own landing page.
        """
        if not candidate:
            return self.return_url or "/"
        parsed = urlparse(candidate)
        if not parsed.scheme and not parsed.netloc:
            return candidate if candidate.startswith("/") else self.return_url or "/"
        if parsed.scheme in ("http", "https") and parsed.hostname in self.allowed_return_hosts:
            return candidate
        return self.return_url or "/"

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
        """Keyed on price_id, not amount: Stripe owns the price, so a plan priced only there
        was being rejected as unpurchasable and never had its live price fetched.
        """
        return [p for p in self.plans if p.price_id]