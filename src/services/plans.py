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
        currency: str = "usd", interval: str = "month", trial_days: int = 0,
        features: list[str] | None = None, unit: str = "month", status: str = "active",
        contact: bool = False, description: str | None = None,
    ) -> Plan:
        """Create or update; the code is derived from the name unless given. `amount=None` keeps the
        saved price; a different amount, unit or interval makes a new Stripe price."""
        if not await self._db.get_row(Default.APPS, app_id):
            raise AdminError("That app no longer exists.")
        code = code.strip() or slug(name)
        if not code:
            raise AdminError("Give the plan a name.")
        _check_status(status)
        if unit != "month" and unit not in UNIT_SECONDS:
            raise AdminError(f"Unknown unit '{unit}'. Use month or one of: {', '.join(UNIT_SECONDS)}.")
        current = await self.get(app_id, code)
        amount = (current.amount if current else None) if amount is None else amount
        wanted = Plan(
            app=app_id, code=code, name=name.strip() or (current.name if current else code),
            description=description if description is not None else current and current.description,
            amount=None if contact else amount or None, currency=currency.lower(), unit=unit,
            interval="month" if unit != "month" else interval, trial_days=trial_days or None,
            features=features or [], status=status, contact=contact,
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
