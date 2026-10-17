from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, computed_field

from src.models.db import STORED

PlanStatus = Literal["active", "soon", "hidden"]
PLAN_STATUSES: tuple[str, ...] = ("active", "soon", "hidden")
#: Metered units and their length; usage arrives in seconds and the Stripe price divides by this.
UNIT_SECONDS = {"minute": 60, "hour": 3600}


class Plan(BaseModel):
    """A Stripe Product (metadata app, code, status, unit, contact, trialDays; features; images[0])
    and its default Price. `amount` is minor units per `unit`; None is free or priced on request."""

    app: str = ""
    code: str
    name: str
    description: str | None = None
    product_id: str | None = None
    price_id: str | None = None
    meter_id: str | None = None
    amount: int | None = None
    currency: str = "usd"
    interval: str = "month"
    unit: str = Field(default="month", description="month, or a metered unit: minute, hour")
    trial_days: int | None = None
    status: PlanStatus = "active"
    contact: bool = Field(default=False, description="Priced on request, never purchasable.")
    features: list[str] = Field(default_factory=list)
    icon: str | None = None

    @computed_field
    @property
    def active(self) -> bool:
        """Sold: status active and not priced on request."""
        return self.status == "active" and not self.contact

    @property
    def metered(self) -> bool:
        return self.unit != "month"


class AppConfig(BaseModel):
    """One calling app (`apps/{appId}`) with its plans, which Stripe holds."""

    model_config = STORED

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
    tags: list[str] = Field(
        default_factory=list, description="Labels for finding an app later: a client, an environment."
    )
    plans: list[Plan] = Field(default_factory=list)

    @classmethod
    def of(cls, app_id: str, row: dict[str, Any], plans: list[Plan]) -> "AppConfig":
        return cls.model_validate(row | {"id": app_id, "plans": plans})

    def resolve_return_url(self, candidate: str | None) -> str:
        """A caller-supplied redirect when allow-listed, else the app's own landing page: `/return`
        and `/cancel` are reachable without a token, so anything else would be an open redirect."""
        if not candidate:
            return self.return_url or "/"
        parsed = urlparse(candidate)
        if not parsed.scheme and not parsed.netloc:
            return candidate if candidate.startswith("/") else self.return_url or "/"
        if parsed.scheme in ("http", "https") and parsed.hostname in self.allowed_return_hosts:
            return candidate
        return self.return_url or "/"

    def plan(self, code: str | None) -> Plan | None:
        return next((p for p in self.plans if p.code == code), None) if code else None

    @property
    def free_plan(self) -> Plan | None:
        return self.plan(self.free_plan_code)
