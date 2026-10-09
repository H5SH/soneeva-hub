"""Users and roles — the /admin/users and /admin/roles screens."""

from app.models.admin import SUPERADMIN, Scope
from cli.context import Context
from cli.output import Printer


async def list_users(ctx: Context, out: Printer, args) -> None:
    out.table(
        [
            {"uid": u.uid, "email": u.email, "role": u.role or "—",
             "apps": "all" if u.role == SUPERADMIN else (",".join(u.apps) or "—"),
             "disabled": u.disabled}
            for u in await ctx.admin.list_users()
        ],
        ["uid", "email", "role", "apps", "disabled"],
    )


async def create_user(ctx: Context, out: Printer, args) -> None:
    """Creates the Firebase account and prints a link for them to set their own password."""
    uid, link = await ctx.auth.create_user(args.email)
    out.detail({"uid": uid, "email": args.email, "password_reset_link": link})


async def save_user(ctx: Context, out: Printer, args) -> None:
    apps = [a.strip() for a in (args.apps or "").split(",") if a.strip()]
    await ctx.admin.save_user(args.uid, args.role or "", apps, args.disabled)
    out.done(f"Updated {args.uid}: role={args.role or '—'} apps={apps or '—'}.")


async def delete_user(ctx: Context, out: Printer, args) -> None:
    await ctx.auth.delete_user(args.uid)
    out.done(f"Removed {args.uid} from Auth and the database.")


async def list_roles(ctx: Context, out: Printer, args) -> None:
    holders = await ctx.admin.role_holders()
    out.table(
        [
            {"role": role, "holders": holders.get(role, 0), "scopes": len(scopes)}
            for role, scopes in (await ctx.admin.list_roles()).items()
        ],
        ["role", "holders", "scopes"],
    )


async def show_role(ctx: Context, out: Printer, args) -> None:
    roles = await ctx.admin.list_roles()
    if args.role not in roles:
        raise SystemExit(f"No role '{args.role}'.")
    out.detail({"role": args.role, "scopes": sorted(roles[args.role])})


async def save_role(ctx: Context, out: Printer, args) -> None:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    if args.scopes.strip() == "all":
        scopes = [str(s) for s in Scope]
    unknown = [s for s in scopes if s not in {str(x) for x in Scope}]
    if unknown:
        raise SystemExit(f"Unknown scope(s): {', '.join(unknown)}. See `scopes`.")

    existing = await ctx.admin.list_roles()
    if args.role in existing:
        await ctx.admin.save_role(args.role, scopes)
        out.done(f"Role '{args.role}' now has {len(scopes)} scope(s).")
    else:
        name = await ctx.admin.create_role(args.role, scopes)
        out.done(f"Role '{name}' created with {len(scopes)} scope(s).")


async def delete_role(ctx: Context, out: Printer, args) -> None:
    await ctx.admin.delete_role(args.role)
    out.done(f"Role '{args.role}' removed.")


async def list_scopes(ctx: Context, out: Printer, args) -> None:
    out.table([{"scope": str(s)} for s in Scope], ["scope"])
