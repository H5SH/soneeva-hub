"""Internal tools: hub keys and scopes, never Stripe. Only here and on /recover: no HTTP API."""

from cli.context import Context
from cli.output import Printer
from src.models.staff import tool_view
from src.services.tokens.generate import hub_env_block


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


async def list_tools(ctx: Context, out: Printer, args) -> None:
    views = [tool_view(t) for t in await ctx.tools.every()]
    out.emit({"tools": views}, lambda: out.table(
        [v | {"keys": len(v["keys"]), "rotation": "redeploy" if v["subjectKeys"] else "by hand"}
         for v in views],
        ["id", "name", "status", "keys", "rotation"],
    ))


async def show_tool(ctx: Context, out: Printer, args) -> None:
    view = tool_view(await ctx.tools.require(args.tool_id))
    out.emit(view, lambda: out.detail(view))


async def create_tool(ctx: Context, out: Printer, args) -> None:
    view = tool_view(await ctx.tools.create(args.tool_id, args.name or args.tool_id, _list(args.scopes)))
    out.emit(view, lambda: out.done(f"Created '{args.tool_id}'. Next: `tool:key:rotate {args.tool_id}`."))


async def update_tool(ctx: Context, out: Printer, args) -> None:
    scopes = None if args.scopes is None else _list(args.scopes)
    fields = {k: v for k, v in {"name": args.name, "scopes": scopes}.items() if v is not None}
    if not fields:
        raise SystemExit("Nothing to change — pass --name or --scopes.")
    view = tool_view(await ctx.tools.update(args.tool_id, **fields))
    out.emit(view, lambda: out.done(f"Updated '{args.tool_id}': {', '.join(fields)}."))


async def delete_tool(ctx: Context, out: Printer, args) -> None:
    await ctx.tools.delete(args.tool_id)
    out.emit({"deleted": True}, lambda: out.done(f"Deleted tool '{args.tool_id}'."))


async def rotate_key(ctx: Context, out: Printer, args) -> None:
    """Shown once and never stored; old keys retire. Refused for keys per business (redeploy instead)."""
    kid, private_pem = await ctx.tools.rotate_key(args.tool_id)
    payload = {"toolId": args.tool_id, "kid": kid, "privateKey": private_pem}
    out.emit(payload, lambda: out.block(hub_env_block(args.tool_id, kid, private_pem)))
