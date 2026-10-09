"""Coupons, as Stripe promotion codes at 100% off."""

import secrets

from app.models.admin import Coupon
from app.models.db import Collection
from app.services.admin import AdminError
from app.services.db import Database
from app.services.payment import PaymentGateway

MIN_LENGTH = 8
# No O/0 or I/1 — these get read aloud and typed by hand.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate(length: int = 10) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


class CouponService:
    def __init__(self, db: Database, payment: PaymentGateway) -> None:
        self._db = db
        self._payment = payment

    async def list_for_app(self, app_id: str) -> list[Coupon]:
        rows = await self._db.get_rows(Collection.COUPONS, filters={"app_id": app_id})
        return [Coupon(**row) for row in rows]

    async def create(
        self,
        app_id: str,
        code: str,
        product_ids: list[str],
        created_by: str,
        max_redemptions: int | None = None,
    ) -> Coupon:
        # Everything checkable from the arguments is checked first, so a bad request costs
        # no round trips.
        code = (code or generate()).strip().upper()
        if len(code) < MIN_LENGTH:
            raise AdminError(f"Codes must be at least {MIN_LENGTH} characters.")
        if not code.isalnum():
            raise AdminError("Codes may contain letters and numbers only.")
        if not product_ids:
            raise AdminError("Add a paid plan first — there is nothing to discount.")
        if await self._db.get_row(Collection.COUPONS, code):
            raise AdminError(f"'{code}' already exists.")

        coupon_id, promo_id = await self._payment.create_full_discount(
            code, app_id, product_ids, max_redemptions
        )
        coupon = Coupon(
            code=code,
            app_id=app_id,
            stripe_coupon_id=coupon_id,
            stripe_promotion_code_id=promo_id,
            created_by=created_by,
            max_redemptions=max_redemptions,
        )
        await self._db.set_row(Collection.COUPONS, code, coupon.model_dump())
        return coupon

    async def deactivate(self, app_id: str, code: str) -> None:
        row = await self._db.get_row(Collection.COUPONS, code)
        if not row:
            raise AdminError("No such coupon.")
        coupon = Coupon(**row)
        # Ownership is re-checked here as well as in the route: a coupon code is global to the
        # Stripe account, so getting this wrong would let one app disable another's.
        if coupon.app_id != app_id:
            raise AdminError("That coupon belongs to a different app.")
        await self._payment.delete_promotion_code(coupon.stripe_promotion_code_id)
        await self._db.set_row(Collection.COUPONS, code, {"active": False})
        await self._db.delete_row(Collection.COUPONS, code)
