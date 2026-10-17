"""Platform people and roles, as /v1/people and /v1/roles keep them. No guards: the CLI is the operator."""

import secrets

from cli.context import Context
from cli.output import Printer
from src.auth import AccountError
from src.services.errors import AdminError
from src.services.people.roles import role_name
from src.services.people.rules import catalogue, known


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


async def list_people(ctx: Context, out: Printer, args) -> None:
    rows, token = [], None
    while True:
        accounts, token = await ctx.accounts.page(token)
        rows += [a for a in accounts if not a.businessId]
        if not token:
            break
    profiles = await ctx.people.store.profiles([a.uid for a in rows])
    out.table([{"uid": a.uid, "email": a.email, "role": profiles.get(a.uid, {}).get("role") or "—",
                "apps": ",".join(profiles.get(a.uid, {}).get("apps", [])) or "—", "disabled": a.disabled}
               for a in rows], ["uid", "email", "role", "apps", "disabled"])


async def create_person(ctx: Context, out: Printer, args) -> None:
    """Creates the account with a random password and prints a link for them to set their own."""
    email = args.email.strip().lower()
    handle = email.split("@")[0]
    try:
        account = await ctx.accounts.create(email, secrets.token_urlsafe(32), args.name or handle,
                                            {"username": handle})
    except AccountError as error:
        raise AdminError(str(error)) from error
    await ctx.people.store.save(account.uid, {"email": email, "role": args.role, "apps": _list(args.apps),
                                              "businesses": _list(args.businesses), "phone": args.phone or "",
                                              "disabled": False})
    link = await ctx.accounts.reset_link(email)
    out.detail({"uid": account.uid, "email": email, "password_reset_link": link})


async def save_person(ctx: Context, out: Printer, args) -> None:
    """An existing account without a record gets one: how the first superadmin is made."""
    account = await ctx.accounts.get(args.uid)
    if not account:
        raise AdminError("No such person.")
    fields: dict = {"email": account.email, "role": args.role, "disabled": args.disabled}
    for name in ("apps", "businesses"):
        if getattr(args, name) is not None:
            fields[name] = _list(getattr(args, name))
    if args.phone is not None:
        fields["phone"] = args.phone
    await ctx.people.store.save(args.uid, fields)
    ctx.roles.forget(args.role or "")
    out.done(f"Updated {args.uid}: role={args.role or '—'}.")


async def delete_person(ctx: Context, out: Printer, args) -> None:
    await ctx.people.store.remove(args.uid)
    await ctx.people.store.revoke_all(args.uid)
    await ctx.accounts.delete(args.uid)
    out.done(f"Removed {args.uid} from Auth and the database.")


async def list_roles(ctx: Context, out: Printer, args) -> None:
    roles = await ctx.people.store.roles()
    out.table([{"role": name, "holders": await ctx.people.store.holders(name), "scopes": len(scopes)}
               for name, scopes in sorted(roles.items())], ["role", "holders", "scopes"])


async def show_role(ctx: Context, out: Printer, args) -> None:
    roles = await ctx.people.store.roles()
    if args.role not in roles:
        raise SystemExit(f"No role '{args.role}'.")
    out.detail({"role": args.role, "scopes": sorted(roles[args.role])})


async def save_role(ctx: Context, out: Printer, args) -> None:
    """`--scopes all`: `*` (every console id) and every payment and platform scope."""
    every = ["*", *(e["id"] for e in catalogue() if e["group"] in ("payments", "platform"))]
    scopes = every if args.scopes.strip() == "all" else _list(args.scopes)
    unknown = [s for s in scopes if s not in known()]
    if unknown:
        raise SystemExit(f"Unknown scope(s): {', '.join(unknown)}. See `scopes`.")
    name = role_name(args.role)
    await ctx.people.store.save_roles({name: scopes})
    ctx.roles.forget(name)
    out.done(f"Role '{name}' has {len(scopes)} scope(s).")


async def delete_role(ctx: Context, out: Printer, args) -> None:
    holders = await ctx.people.store.holders(args.role)
    if holders:
        raise AdminError(f"{holders} person(s) still have this role. Move them off it first.")
    await ctx.people.store.save_roles({args.role: None})
    ctx.roles.forget(args.role)
    out.done(f"Role '{args.role}' removed.")


async def list_scopes(ctx: Context, out: Printer, args) -> None:
    out.table(catalogue(), ["id", "group", "twin"])
