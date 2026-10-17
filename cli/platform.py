"""Platform commands: the hub key, seeds and businesses."""

from pathlib import Path

from cli.context import Context
from cli.output import Printer
from src.db import Write
from src.models.admin import Scope
from src.models.db import Default
from src.services.catalogue import read_console_scopes, save_catalogue, seed_platform_roles
from src.services.errors import AdminError
from src.services.voice import VOICE_APP, VOICE_TOOL
from src.tunables import tunable

#: Exactly what dashboard/lib/services/restapi.ts calls; the console signs as the `soneeva` app.
CONSOLE_SCOPES = [
    Scope.HUB_BUSINESS_CREATE, Scope.HUB_BUSINESS_VIEW, Scope.HUB_BUSINESS_UPDATE,
    Scope.HUB_SERVICEPERSON_VIEW, Scope.HUB_VOICE_UPDATE, Scope.HUB_NUMBER_VIEW, Scope.HUB_NUMBER_BUY,
    Scope.HUB_ACCOUNT_VIEW, Scope.HUB_ACCOUNT_CREATE, Scope.HUB_ACCOUNT_UPDATE, Scope.HUB_ACCOUNT_DELETE,
    Scope.HUB_ACCOUNT_RECOVER, Scope.HUB_BILLING_VIEW, Scope.HUB_BILLING_UPDATE, Scope.HUB_HANDOFF_REDEEM,
    Scope.HUB_MEDIA_VIEW, Scope.HUB_MEDIA_UPDATE,
]
ADMIN_SCOPES = [
    Scope.HUB_PEOPLE_VIEW, Scope.HUB_PEOPLE_UPDATE, Scope.HUB_ACCOUNT_RECOVER, Scope.HUB_PLATFORM_VIEW,
    Scope.HUB_BUSINESS_VIEW, Scope.HUB_BUSINESS_UPDATE, Scope.HUB_SERVICEPERSON_VIEW, Scope.HUB_PAYMENT_VIEW,
    Scope.HUB_PAYMENT_UPDATE, Scope.HUB_HANDOFF_CREATE, Scope.HUB_MEDIA_VIEW, Scope.HUB_MEDIA_UPDATE,
    Scope.HUB_ADVERTISEMENT_VIEW, Scope.HUB_ADVERTISEMENT_UPDATE,
]
VOICE_SCOPES = [Scope.HUB_USAGE_WRITE, Scope.HUB_PLAN_VIEW]
ADMIN_TOOL = "karbon-admin"
#: Never apps: the old console registration (it signs as `soneeva` now) and the hub itself. Removed
#: from `apps` when seeding; the tools move to `tools` with their keys.
NOT_APPS = ("soneeva-console", "karbon-hub")
# code, name, unit, amount in minor units per unit (None = on request), status
SONEEVA_PLANS = [
    ("per-minute", "Per minute", "minute", 10, "active"),
    ("base", "Base", "hour", 1000, "soon"),
    ("pro", "Pro", "hour", 2000, "soon"),
    ("premium", "Premium", "minute", 100, "soon"),
    ("enterprise", "Enterprise", "month", None, "soon"),
]


async def _drop_app(ctx: Context, app_id: str) -> None:
    """A real delete, for this migration only: a moved or stale registration must leave `apps`
    entirely (a soft-deleted doc would still show), unlike every other delete, which is soft."""
    if await ctx.db.get(Default.APPS, app_id) is not None:
        await ctx.db.batch([Write(Default.APPS, app_id)])


async def _ensure_tool(ctx: Context, tool_id: str, name: str, scopes: list[Scope], per_business: bool
                       ) -> None:
    """Created, or moved from an `apps/{id}` registration with its keys; scopes always reset."""
    old = await ctx.db.get_row(Default.APPS, tool_id)
    if old and not await ctx.tools.get(tool_id):
        await ctx.db.set_row(Default.TOOLS, tool_id, {"name": old.get("name") or name, "status": "active",
                                                     "keys": old.get("keys", []), "scopes": []})
    await _drop_app(ctx, tool_id)
    if not await ctx.tools.get(tool_id):
        await ctx.tools.create(tool_id, name, [])
    await ctx.tools.update(tool_id, scopes=[str(s) for s in scopes], subject_keys=per_business)


async def seed_soneeva(ctx: Context, out: Printer, args) -> None:
    """Idempotent: the app `soneeva` (plans, billing; the console signs as it), the internal tools
    `karbon-admin` and `soneeva-voice` (moved out of `apps` with their keys), no stale `apps` docs,
    the scope catalogue and the plans missing from Stripe (live, and test when STRIPE_TEST_SECRET_KEY
    is set)."""
    app_id = VOICE_APP
    if not await ctx.admin.get_app(app_id):
        await ctx.admin.create_app(app_id, "Soneeva", "", ["localhost", "soneeva.com", "console.soneeva.com"])
    await ctx.admin.update_app(app_id, scopes=[str(s) for s in CONSOLE_SCOPES])
    for stale in NOT_APPS:
        await _drop_app(ctx, stale)
        ctx.keys.forget(stale)
    await _ensure_tool(ctx, VOICE_TOOL, "Soneeva voice", VOICE_SCOPES, per_business=True)
    await _ensure_tool(ctx, ADMIN_TOOL, "Karbon admin", ADMIN_SCOPES, per_business=False)
    await save_catalogue(ctx.db, app_id, tunable(f"scopes.{app_id}"))
    for mode in ctx.plans.modes():
        plans = ctx.plans[mode]
        for code, name, unit, amount, status in SONEEVA_PLANS:
            # Only missing plans: an existing one is Stripe's, and may have been edited since.
            if not await plans.get(app_id, code):
                await plans.define(app_id, code, name, amount or 0, tunable("plans.currency"), unit=unit,
                                   status=status, contact=amount is None)
    roles = await seed_platform_roles(ctx.db)
    for role in roles:
        ctx.roles.forget(role)
    modes = " and ".join(ctx.plans.modes())
    out.done(f"Seeded '{app_id}' ({modes}): {len(SONEEVA_PLANS)} plans, per-minute active, the rest "
             f"coming soon; tools {ADMIN_TOOL}, {VOICE_TOOL}; roles updated "
             f"(missing ones created, hub.* dropped): {', '.join(roles) or 'none'}.")


async def sync_scopes(ctx: Context, out: Printer, args) -> None:
    """`scopes/{app}` from the console's scopes.json, the one source of scope ids."""
    scopes = read_console_scopes(Path(args.path))
    await save_catalogue(ctx.db, args.app_id, scopes)
    out.done(f"scopes/{args.app_id}: {len(scopes)} scopes.")


async def list_businesses(ctx: Context, out: Printer, args) -> None:
    out.table(
        [b.model_dump(mode="json") | {"numbers": len(b.numbers)} for b in await ctx.businesses.every()],
        ["id", "name", "type", "status", "provisioned", "voiceUrl", "numbers"],
    )


async def deploy_business(ctx: Context, out: Printer, args) -> None:
    if not await ctx.businesses.get(args.business_id):
        raise AdminError(f"No business '{args.business_id}'.")
    out.done(f"Voice for {args.business_id} at {await ctx.voice.deploy(args.business_id)}.")


async def provision_business(ctx: Context, out: Printer, args) -> None:
    """Runs a sign-up's provisioning again; every step is idempotent."""
    business = await ctx.businesses.provisioner.run(args.business_id)
    out.done(f"{business.id} provisioned ({business.dbId}).")


async def delete_business(ctx: Context, out: Printer, args) -> None:
    """Sets status `deleted` (like the dashboard); its database and owner account stay."""
    business = await ctx.businesses.delete(args.business_id, "cli")
    out.done(f"{business.id} deleted. Its database {business.dbId} and owner {business.ownerUid} remain.")


async def prune_db(ctx: Context, out: Printer, args) -> None:
    """Top-level collections of (default) that no code uses (not in `Default`); `--apply` deletes them."""
    unused = [c for c in await ctx.db.collections() if c not in {d.value for d in Default}]
    if args.apply:
        for name in unused:
            await ctx.db.drop_collection(name)
    verb = "Deleted" if args.apply else "Unused (run with --apply to delete)"
    out.emit({"unused": unused, "deleted": bool(args.apply)},
             lambda: out.done(f"{verb}: {', '.join(unused) or 'none'}."))
