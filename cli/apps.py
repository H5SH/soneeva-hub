"""Apps, keys, webhooks and plans. `--json` prints exactly what the matching /v1/payment route returns.
Where the route takes `{appId}_staging` (plans, webhook, subscription), so does the command: test mode."""

import mimetypes
from pathlib import Path

from cli.context import OPERATOR, Context
from cli.output import Printer
from src.models.mode import staged
from src.models.staff import app_view, plan_view
from src.services.errors import AdminError
from src.services.tokens.generate import env_block


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


async def _view(ctx: Context, app_id: str) -> dict:
    base, mode = staged(app_id)
    config = await ctx.admin.get_app(base, mode)
    if not config:
        raise AdminError(f"No app '{base}'.")
    return app_view(config, await ctx.admin.get_application(base), mode)


async def list_apps(ctx: Context, out: Printer, args) -> None:
    """GET /v1/payment/apps?q= — `--q` matches the id, the name or a tag."""
    found = await ctx.admin.list_apps(OPERATOR, args.q or "")
    views = [app_view(config, creds) for config, creds in found]
    out.emit({"apps": views}, lambda: out.table(
        [{"id": v["id"], "name": v["name"], "tags": ",".join(v["tags"]) or "—", "plans": len(v["plans"]),
          "keys": len(v["keys"]), "webhook": "own" if v["hasWebhookSecret"] else "fallback"} for v in views],
        ["id", "name", "tags", "plans", "keys", "webhook"],
    ))


async def show_app(ctx: Context, out: Printer, args) -> None:
    subscribers = await ctx.admin.subscribers(staged(args.app_id)[0])
    view = await _view(ctx, args.app_id) | {"subscribers": subscribers}
    out.emit(view, lambda: out.detail({
        "id": view["id"], "mode": view["mode"], "subscribers": view["subscribers"], "name": view["name"],
        "tags": view["tags"], "free_plan": view["freePlanCode"],
        "return_url": view["returnUrl"], "allowed_hosts": view["allowedReturnHosts"],
        "webhook_secret": "set" if view["hasWebhookSecret"] else "not set (the shared fallback is used)",
        "keys": [{"kid": k["kid"], "status": k["status"]} for k in view["keys"]], "scopes": view["scopes"],
        "plans": [{"code": p["code"], "name": p["name"], "amount": p["amount"], "unit": p["unit"],
                   "status": p["status"], "product": p["productId"], "price": p["priceId"]}
                  for p in view["plans"]],
    }))


async def create_app(ctx: Context, out: Printer, args) -> None:
    config = await ctx.admin.create_app(
        args.app_id.strip().lower(), args.name or args.app_id, args.return_url or "", _list(args.hosts),
        _list(args.tags),
    )
    view = await _view(ctx, config.id)
    out.emit(view, lambda: out.done(f"Created '{config.id}'. Add plans, then `key:rotate {config.id}`."))


async def update_app(ctx: Context, out: Printer, args) -> None:
    """--webhook-secret is write-only: stored, never printed back."""
    fields: dict[str, object] = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.return_url is not None:
        fields["return_url"] = args.return_url or None
    if args.hosts is not None:
        fields["allowed_return_hosts"] = _list(args.hosts)
    if args.free_plan is not None:
        fields["free_plan_code"] = args.free_plan or None
    if args.scopes is not None:
        fields["scopes"] = _list(args.scopes)
    if args.tags is not None:
        fields["tags"] = _list(args.tags)
    if args.webhook_secret:
        fields["stripe_webhook_secret"] = args.webhook_secret.strip()
    if not fields:
        raise SystemExit("Nothing to change — pass --name, --return-url, --hosts, --free-plan, --scopes, "
                         "--tags or --webhook-secret.")
    await ctx.admin.update_app(args.app_id, **fields)
    view = await _view(ctx, args.app_id)
    out.emit(view, lambda: out.done(f"Updated '{args.app_id}': {', '.join(fields)}."))


async def delete_app(ctx: Context, out: Printer, args) -> None:
    await ctx.admin.delete_app(args.app_id)
    out.emit({"deleted": True}, lambda: out.done(f"Deleted '{args.app_id}'."))


async def rotate_key(ctx: Context, out: Printer, args) -> None:
    """Shown once and never stored, exactly as POST /v1/payment/apps/{id}/keys does it."""
    kid, private_pem = await ctx.admin.rotate_key(args.app_id)
    payload = {"appId": args.app_id, "kid": kid, "privateKey": private_pem}
    out.emit(payload, lambda: out.block(env_block(args.app_id, kid, private_pem)))


async def provision_webhook(ctx: Context, out: Printer, args) -> None:
    """POST /v1/payment/apps/{id}/webhook: the endpoint is created, its secret stored and never shown.
    `{appId}_staging`: the staging endpoint, in the Stripe test account."""
    base_url = ctx.settings.service_base_url if ctx.settings else ""
    app_id, mode = staged(args.app_id)
    endpoint_id, url = await ctx.admin.provision_webhook(app_id, base_url, mode)
    payload = {"endpointId": endpoint_id, "url": url, "hasWebhookSecret": True}
    shown = {"endpoint": endpoint_id, "url": url, "secret": "stored, not shown"}
    out.emit(payload, lambda: out.detail(shown))


async def list_plans(ctx: Context, out: Printer, args) -> None:
    plans = (await _view(ctx, args.app_id))["plans"]
    out.emit({"plans": plans}, lambda: out.table(
        [p | {"price": p["priceId"] or "—"} for p in plans],
        ["code", "name", "amount", "currency", "unit", "status", "productId", "price"],
    ))


async def save_plan(ctx: Context, out: Printer, args) -> None:
    """The code is derived from --name when omitted. --amount is minor units per --unit
    (`--unit minute --amount 10` is $0.10 a minute); unset keeps the saved amount, a different one makes
    a new Stripe price. --status active|soon|hidden; unset keeps it."""
    if not (args.code or args.name):
        raise SystemExit("Give the plan a code or a --name.")
    app_id, mode = staged(args.app_id)
    plans = ctx.plans[mode]
    saved = await plans.get(app_id, args.code) if args.code else None
    features = [f.strip() for f in (args.features or "").split("|") if f.strip()]
    plan = await plans.define(
        app_id, args.code or "", args.name or "", args.amount, args.currency, args.interval,
        args.trial, features or (saved.features if saved else []),
        unit=args.unit or (saved.unit if saved else "month"),
        status=args.status or (saved.status if saved else "active"), contact=args.contact,
    )
    priced = f"price={plan.price_id}" if plan.price_id else "no price"
    out.emit(plan_view(plan), lambda: out.done(f"Plan '{plan.code}' saved ({plan.product_id}, {priced})."))


async def set_plan_icon(ctx: Context, out: Printer, args) -> None:
    """PUT /plans/{code}/icon: the image is uploaded to Stripe and set on the product."""
    path = Path(args.path)
    kind = mimetypes.guess_type(path.name)[0] or ""
    app_id, mode = staged(args.app_id)
    plan = await ctx.plans[mode].set_icon(app_id, args.code, path.read_bytes(), kind)
    out.emit(plan_view(plan), lambda: out.done(f"Plan '{plan.code}' icon: {plan.icon}"))


async def set_plan_status(ctx: Context, out: Printer, args) -> None:
    """PUT /plans/{code}/status: the product's metadata.status."""
    app_id, mode = staged(args.app_id)
    plan = await ctx.plans[mode].set_status(app_id, args.code, args.status)
    out.emit(plan_view(plan), lambda: out.done(f"Plan '{plan.code}' is {plan.status}."))


async def delete_plan(ctx: Context, out: Printer, args) -> None:
    app_id, mode = staged(args.app_id)
    await ctx.plans[mode].delete(app_id, args.code)
    out.emit({"deleted": True}, lambda: out.done(
        f"Plan '{args.code}' archived in Stripe; subscribers keep billing."))


async def show_subscription(ctx: Context, out: Printer, args) -> None:
    """What the consuming app sees at GET /v1/apps/{id}/entitlements."""
    app_id, mode = staged(args.app_id)
    config = await ctx.admin.get_app(app_id, mode)
    if not config:
        raise AdminError(f"No app '{app_id}'.")
    billing = ctx.billing[mode]
    record = await billing.get_record(app_id, args.tenant)
    data = billing.to_entitlements(config, record).model_dump(mode="json")
    out.emit(data, lambda: out.detail(data))
