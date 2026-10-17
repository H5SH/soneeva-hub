"""Coupons: Stripe promotion codes with discount terms, scoped to one app's products."""

import secrets

from src.models.discount import Coupon, Discount
from src.payment import PaymentGateway
from src.services.errors import AdminError
from src.services.plans import PlanService

MIN_LENGTH = 8
# No O/0 or I/1 — these get read aloud and typed by hand.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate(length: int = 10) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


class CouponService:
    def __init__(self, payment: PaymentGateway, plans: PlanService) -> None:
        self._payment = payment
        self._plans = plans

    async def list_for_app(self, app_id: str) -> list[Coupon]:
        return await self._payment.list_coupons(app_id)

    async def create(
        self, app_id: str, code: str, created_by: str, discount: Discount | None = None
    ) -> Coupon:
        """Defaults to 100% off forever (a free code). Terms were validated by `Discount` already."""
        code = (code or generate()).strip().upper()
        if len(code) < MIN_LENGTH:
            raise AdminError(f"Codes must be at least {MIN_LENGTH} characters.")
        if not code.isalnum():
            raise AdminError("Codes may contain letters and numbers only.")
        # A coupon with no products is global to the Stripe account and would discount every app.
        product_ids = [p.product_id for p in await self._plans.for_app(app_id) if p.price_id and p.product_id]
        if not product_ids:
            raise AdminError("Add a paid plan first — there is nothing to discount.")
        if await self._payment.find_coupon(code):
            raise AdminError(f"'{code}' already exists.")
        terms = discount or Discount()
        return await self._payment.create_coupon(app_id, code, product_ids, terms, created_by)

    async def deactivate(self, app_id: str, code: str) -> None:
        coupon = await self._payment.find_coupon(code.strip().upper())
        if not coupon:
            raise AdminError("No such coupon.")
        # A code is global to the Stripe account: never let one app disable another's.
        if coupon.app_id != app_id:
            raise AdminError("That coupon belongs to a different app.")
        await self._payment.delete_coupon(coupon)
