"""Coupons and businesses as billing customers. `--json` prints what the matching route returns."""

from datetime import datetime

from cli.context import OPERATOR, Context
from cli.output import Printer
from src.models.discount import Discount, DiscountKind
from src.models.mode import staged
from src.models.staff import coupon_view, invoice_view, plan_view, subscription_view
from src.services.errors import AdminError


async def list_coupons(ctx: Context, out: Printer, args) -> None:
    app_id, mode = staged(args.app_id)
    coupons = await ctx.coupons[mode].list_for_app(app_id)
    out.emit({"coupons": [coupon_view(c) for c in coupons]}, lambda: out.table(
        [{"code": c.code, "discount": c.discount.describe(), "active": c.active,
          "max": c.discount.max_redemptions or "∞", "by": c.created_by} for c in coupons],
        ["code", "discount", "active", "max", "by"],
    ))


async def create_coupon(ctx: Context, out: Printer, args) -> None:
    """`--amount` is minor units (1000 is $10.00); otherwise `--percent` (default 100). Terms that do
    not fit together are refused before Stripe is called."""
    try:
        terms = Discount.build(
            kind=DiscountKind.AMOUNT if args.amount else DiscountKind.PERCENT,
            value=float(args.amount or args.percent), currency=args.currency, duration=args.duration,
            months=args.months or None, max_redemptions=args.max or None,
            expires_at=datetime.fromisoformat(args.expires) if args.expires else None,
        )
    except ValueError as error:
        raise AdminError(str(error)) from error
    app_id, mode = staged(args.app_id)
    coupon = await ctx.coupons[mode].create(app_id, args.code or "", OPERATOR.email, terms)
    out.emit(coupon_view(coupon), lambda: out.done(f"Coupon {coupon.code} — {terms.describe()}."))


async def delete_coupon(ctx: Context, out: Printer, args) -> None:
    app_id, mode = staged(args.app_id)
    await ctx.coupons[mode].deactivate(app_id, args.code)
    out.emit({"deleted": True}, lambda: out.done(f"Coupon {args.code} deactivated."))


async def list_customers(ctx: Context, out: Printer, args) -> None:
    """GET /v1/payment/businesses."""
    rows = await ctx.customers.every(OPERATOR)
    payload = {"businesses": [{"business": b.model_dump(mode="json"), "subscription": subscription_view(r)}
                              for b, r in rows]}
    out.emit(payload, lambda: out.table(
        [{"id": b.id, "name": b.name, "app": b.appId, "mode": b.mode, "status": r.status,
          "plan": r.plan_code or "—"} for b, r in rows],
        ["id", "name", "app", "mode", "status", "plan"],
    ))


async def show_customer(ctx: Context, out: Printer, args) -> None:
    business, record = await ctx.customers.one(OPERATOR, args.business_id)
    app = await ctx.admin.get_app(business.appId, business.mode)
    plan = app.plan(record.plan_code) if app else None
    payload = {"business": business.model_dump(mode="json"), "subscription": subscription_view(record),
               "plan": plan_view(plan) if plan else None}
    out.emit(payload, lambda: out.detail({"id": business.id, "name": business.name, "app": business.appId}
                                         | payload["subscription"]))


async def list_invoices(ctx: Context, out: Printer, args) -> None:
    business, record = await ctx.customers.one(OPERATOR, args.business_id)
    invoices = [invoice_view(i) for i in await ctx.customers.invoices(business, record)]
    out.emit({"invoices": invoices}, lambda: out.table(
        invoices, ["number", "status", "amountDue", "amountPaid", "currency", "created"]))


async def show_usage(ctx: Context, out: Printer, args) -> None:
    usage = await ctx.customers.usage(*await ctx.customers.one(OPERATOR, args.business_id))
    out.emit(usage, lambda: out.table(usage["days"], ["day", "seconds"]))
