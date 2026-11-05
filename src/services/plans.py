"""Plans are Stripe products and prices, read through one short cache for the whole account."""

from typing import Any

from src.db import Database
from src.models.app import PLAN_STATUSES, UNIT_SECONDS, AppConfig, Plan
from src.models.db import Default
from src.payment import PaymentGateway
from src.services.errors import AdminError
from src.services.ttl import MISSING, Ttl
from src.tunables import tunable
from src.utils import slug


def plan_code(code: str | None, name: str) -> str:
    """As given, or derived from the name."""
    found = code or slug(name)
    if not found:
        raise AdminError("Give the plan a name.")
    return found


class PlanService:
    def __init__(self, db: Database, payment: PaymentGateway) -> None:
        self._db = db
        self._payment = payment
        self._cache = Ttl(tunable("stripe.plansCacheSeconds"))

    def forget(self) -> None:
        """After our own writes and on product/price webhooks."""
        self._cache.clear()

    async def for_app(self, app_id: str) -> list[Plan]:
        plans = self._cache.get("all")
        if plans is MISSING:
            plans = self._cache.put("all", await self._payment.list_plans())
        return sorted((p for p in plans if p.app == app_id), key=lambda p: p.code)

    async def get(self, app_id: str, code: str) -> Plan | None:
        return next((p for p in await self.for_app(app_id) if p.code == code), None)

    async def app(self, app_id: str) -> AppConfig | None:
        row = await self._db.get_row(Default.APPS, app_id)
        return AppConfig.of(app_id, row, await self.for_app(app_id)) if row else None

    async def define(
        self, app_id: str, code: str = "", name: str = "", amount: int | None = None,
        currency: str | None = None, interval: str | None = None, trial_days: int | None = None,
        features: list[str] | None = None, unit: str | None = None, status: str | None = None,
        contact: bool | None = None, description: str | None = None,
    ) -> Plan:
        """Create or update; the code is derived from the name unless given. Whatever is None keeps what
        is saved, or takes the default for a new plan; a different amount, unit or interval makes a new
        Stripe price."""
        if not await self._db.get_row(Default.APPS, app_id):
            raise AdminError("That app no longer exists.")
        code = plan_code(code.strip(), name)
        current = await self.get(app_id, code)

        def kept(given: Any, field: str, default: Any) -> Any:
            return given if given is not None else getattr(current, field) if current else default

        status, unit = kept(status, "status", "active"), kept(unit, "unit", "month")
        amount, contact = kept(amount, "amount", None), kept(contact, "contact", False)
        _check_status(status)
        if unit != "month" and unit not in UNIT_SECONDS:
            raise AdminError(f"Unknown unit '{unit}'. Use month or one of: {', '.join(UNIT_SECONDS)}.")
        wanted = Plan(
            app=app_id, code=code, name=name.strip() or (current.name if current else code),
            description=description if description is not None else current and current.description,
            amount=None if contact else amount or None, currency=kept(currency, "currency", "usd").lower(),
            unit=unit, interval="month" if unit != "month" else kept(interval, "interval", "month"),
            trial_days=kept(trial_days, "trial_days", 0) or None, features=kept(features, "features", []),
            status=status, contact=contact,
        )
        plan = await (self._payment.update_plan(current, wanted) if current
                      else self._payment.create_plan(wanted))
        self.forget()
        return plan

    async def set_status(self, app_id: str, code: str, status: str) -> Plan:
        _check_status(status)
        plan = await self._require(app_id, code)
        plan = await self._payment.update_plan(plan, plan.model_copy(update={"status": status}))
        self.forget()
        return plan

    async def set_icon(self, app_id: str, code: str, data: bytes | None, content_type: str = "") -> Plan:
        """The product's image in Stripe; None removes it."""
        plan = await self._require(app_id, code)
        url = await self._payment.set_plan_icon(plan, data, content_type)
        self.forget()
        return plan.model_copy(update={"icon": url})

    async def delete(self, app_id: str, code: str) -> None:
        """Archives the product and its price; subscribers on it keep billing."""
        app = await self.app(app_id)
        if not app:
            raise AdminError("That app no longer exists.")
        if app.free_plan_code == code:
            raise AdminError("That is the free plan. Pick a different free plan first.")
        await self._payment.archive_plan(await self._require(app_id, code))
        self.forget()

    async def public(self, app_id: str) -> list[dict[str, Any]]:
        """Active and coming-soon plans for the site and console, active first.
        `price` is in major units; null for plans priced on request."""
        shown = [{
            "code": p.code, "name": p.name, "price": None if p.contact else (p.amount or 0) / 100,
            "unit": p.unit, "currency": p.currency, "features": p.features, "status": p.status,
            "icon": p.icon,
        } for p in await self.for_app(app_id) if p.status != "hidden"]
        return sorted(shown, key=lambda p: p["status"] != "active")

    async def _require(self, app_id: str, code: str) -> Plan:
        plan = await self.get(app_id, code)
        if not plan:
            raise AdminError(f"No plan '{code}'.")
        return plan


def _check_status(status: str) -> None:
    if status not in PLAN_STATUSES:
        raise AdminError(f"Unknown status '{status}'. Use one of: {', '.join(PLAN_STATUSES)}.")
