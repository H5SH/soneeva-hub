"""Apps, plans, keys, coupons — the /admin/apps screens."""

from app.services.tokens.generate import env_block
from cli.context import OPERATOR, Context
from cli.output import Printer


async def list_apps(ctx: Context, out: Printer, args) -> None:
    apps, keys = await ctx.admin.list_apps_with_keys(OPERATOR)
    out.table(
        [
            {"id": a.id, "name": a.name, "plans": len(a.plans), "keys": keys.get(a.id, 0),
             "free": a.free_plan_code or "—"}
            for a in apps
        ],
        ["id", "name", "plans", "keys", "free"],
    )


async def show_app(ctx: Context, out: Printer, args) -> None:
    config = await ctx.admin.get_app(args.app_id)
    if not config:
        raise SystemExit(f"No app '{args.app_id}'.")
    application = await ctx.admin.get_application(args.app_id)
    out.detail(
        {
            "id": config.id,
            "name": config.name,
            "free_plan": config.free_plan_code,
            "return_url": config.return_url,
            "allowed_hosts": config.allowed_return_hosts,
            "keys": [f"{k.kid} ({k.status})" for k in (application.keys if application else [])],
            "plans": [
                f"{p.code} ({p.name})"
                + (f" price={p.price_id}" if p.price_id else " free")
                + (f" trial={p.trial_days}d" if p.trial_days else "")
                for p in config.plans
            ],
        }
    )


async def create_app(ctx: Context, out: Printer, args) -> None:
    config = await ctx.admin.create_app(
        args.app_id.strip().lower(),
        args.name or args.app_id,
        args.return_url or "",
        [h.strip() for h in (args.hosts or "").split(",") if h.strip()],
    )
    out.done(f"Created '{config.id}'. Add plans, then `key rotate {config.id}`.")


async def update_app(ctx: Context, out: Printer, args) -> None:
    fields: dict[str, object] = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.return_url is not None:
        fields["return_url"] = args.return_url
    if args.hosts is not None:
        fields["allowed_return_hosts"] = [h.strip() for h in args.hosts.split(",") if h.strip()]
    if args.free_plan is not None:
        fields["free_plan_code"] = args.free_plan
    if not fields:
        raise SystemExit("Nothing to change — pass --name, --return-url, --hosts or --free-plan.")
    await ctx.admin.update_app(args.app_id, **fields)
    out.done(f"Updated '{args.app_id}': {', '.join(fields)}.")


async def delete_app(ctx: Context, out: Printer, args) -> None:
    await ctx.admin.delete_app(args.app_id)
    out.done(f"Deleted '{args.app_id}'.")


async def rotate_key(ctx: Context, out: Printer, args) -> None:
    kid, private_pem = await ctx.admin.rotate_key(args.app_id)
    # Shown once and never stored, exactly as the dashboard does it.
    out.block(env_block(args.app_id, kid, private_pem))


async def save_plan(ctx: Context, out: Printer, args) -> None:
    plan = await ctx.admin.define_plan(
        args.app_id,
        args.code,
        args.name or args.code,
        args.amount,
        args.currency,
        args.interval,
        args.trial,
        [f.strip() for f in (args.features or "").split("|") if f.strip()],
    )
    priced = f"price={plan.price_id}" if plan.price_id else "free tier"
    out.done(f"Plan '{plan.code}' saved ({priced}).")


async def delete_plan(ctx: Context, out: Printer, args) -> None:
    await ctx.admin.delete_plan(args.app_id, args.code)
    out.done(f"Plan '{args.code}' removed. Its Stripe price stays, so subscribers keep billing.")


async def list_coupons(ctx: Context, out: Printer, args) -> None:
    out.table(
        [
            {"code": c.code, "app": c.app_id, "active": c.active,
             "max": c.max_redemptions or "∞", "by": c.created_by}
            for c in await ctx.coupons.list_for_app(args.app_id)
        ],
        ["code", "app", "active", "max", "by"],
    )


async def create_coupon(ctx: Context, out: Printer, args) -> None:
    coupon = await ctx.coupons.create(
        args.app_id, args.code or "", OPERATOR.email, args.max or None
    )
    out.done(f"Coupon {coupon.code} — 100% off, forever.")


async def delete_coupon(ctx: Context, out: Printer, args) -> None:
    await ctx.coupons.deactivate(args.app_id, args.code)
    out.done(f"Coupon {args.code} deactivated.")


async def show_subscription(ctx: Context, out: Printer, args) -> None:
    """What the consuming app sees at GET /v1/apps/{id}/entitlements."""
    config = await ctx.admin.get_app(args.app_id)
    if not config:
        raise SystemExit(f"No app '{args.app_id}'.")
    record = await ctx.billing.get_record(args.app_id, args.tenant)
    out.detail(ctx.billing.to_entitlements(config, record).model_dump())
