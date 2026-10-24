"""Everything /v1/payment, /v1/people and /v1/roles do, and the one-time platform setup, from a terminal.

    uv run python -m cli --help

Commands call the same services the routes call, so the two cannot drift. Add a command here
only once its behaviour lives in a service.
"""

import argparse
import asyncio
import sys

from cli import apps, coupons, people, platform, tools
from cli.context import build_context
from cli.output import Printer
from src.services.errors import AdminError, PlatformError

# name, help, handler, positionals, options
COMMANDS = [
    ("apps", "List apps; --q matches a name, id or tag", apps.list_apps, [], ["q"]),
    ("app:show", "Show one app in full", apps.show_app, ["app_id"], []),
    ("app:create", "Create an app", apps.create_app, ["app_id"],
     ["name", "return-url", "hosts", "tags"]),
    ("app:update", "Change an app's fields (--webhook-secret is write-only)", apps.update_app, ["app_id"],
     ["name", "return-url", "hosts", "free-plan", "scopes", "tags", "webhook-secret"]),
    ("app:delete", "Delete an app (never while it has subscribers)", apps.delete_app, ["app_id"], []),
    ("key:rotate", "Issue a key and print the env block", apps.rotate_key, ["app_id"], []),
    ("webhook:create", "Create this app's Stripe endpoint and store its secret ({app}_staging: test)",
     apps.provision_webhook, ["app_id"], []),
    ("tools", "List internal tools", tools.list_tools, [], []),
    ("tool:show", "Show one internal tool", tools.show_tool, ["tool_id"], []),
    ("tool:create", "Create an internal tool", tools.create_tool, ["tool_id"], ["name", "scopes"]),
    ("tool:update", "Change a tool's name or hub scopes", tools.update_tool, ["tool_id"], ["name", "scopes"]),
    ("tool:delete", "Delete an internal tool", tools.delete_tool, ["tool_id"], []),
    ("tool:key:rotate", "Issue a tool key and print the HUB_* env block", tools.rotate_key, ["tool_id"], []),
    ("plans", "List an app's plans", apps.list_plans, ["app_id"], []),
    ("plan:save", "Create or update a plan (code derived from --name when omitted)", apps.save_plan,
     ["app_id", "code?"],
     ["name", "amount", "currency", "interval", "unit", "trial", "features", "status", "contact"]),
    ("plan:icon", "Upload a png, jpeg or webp as the plan's icon", apps.set_plan_icon,
     ["app_id", "code", "path"], []),
    ("plan:status", "Set a plan active, soon or hidden", apps.set_plan_status,
     ["app_id", "code", "status"], []),
    ("plan:delete", "Remove a plan", apps.delete_plan, ["app_id", "code"], []),
    ("coupons", "List an app's coupons", coupons.list_coupons, ["app_id"], []),
    ("coupon:create", "Create a discount code (default 100%% off forever)", coupons.create_coupon,
     ["app_id"], ["code", "percent", "amount", "currency", "duration", "months", "max", "expires"]),
    ("coupon:delete", "Deactivate a code", coupons.delete_coupon, ["app_id", "code"], []),
    ("subscription", "Entitlements as the consuming app sees them",
     apps.show_subscription, ["app_id", "tenant"], []),
    ("customers", "Businesses with their subscriptions", coupons.list_customers, [], []),
    ("customer:show", "One business's subscription and plan", coupons.show_customer, ["business_id"], []),
    ("customer:invoices", "A business's Stripe invoices", coupons.list_invoices, ["business_id"], []),
    ("customer:usage", "A business's metered seconds per day", coupons.show_usage, ["business_id"], []),
    ("people", "List platform people", people.list_people, [], []),
    ("person:create", "Create a person, print a password-set link", people.create_person,
     ["email"], ["name", "role", "apps", "businesses", "phone"]),
    ("person:save", "Set someone's role, apps and businesses (gives an account its first record)",
     people.save_person, ["uid"], ["role", "apps", "businesses", "phone", "disabled"]),
    ("person:delete", "Remove a person", people.delete_person, ["uid"], []),
    ("roles", "List roles", people.list_roles, [], []),
    ("role:show", "Show a role's scopes", people.show_role, ["role"], []),
    ("role:save", "Create or replace a role ('all': * plus every payment and platform scope)",
     people.save_role, ["role"], ["scopes"]),
    ("role:delete", "Remove a role nobody holds", people.delete_role, ["role"], []),
    ("scopes", "List the scopes a role can hold", people.list_scopes, [], []),
    ("seed:soneeva", "Register soneeva, its tools, scopes and plans", platform.seed_soneeva, [], []),
    ("db:prune", "List (and with --apply delete) unused top-level collections", platform.prune_db,
     [], ["apply"]),
    ("scopes:sync", "Write scopes/{app} from a console scopes.json", platform.sync_scopes,
     ["app_id", "path"], []),
    ("businesses", "List businesses", platform.list_businesses, [], []),
    ("business:provision", "Provision a signed-up business (again)", platform.provision_business,
     ["business_id"], []),
    ("business:delete", "Soft-delete a business row (not its database or owner account)",
     platform.delete_business, ["business_id"], []),
]

# Options that are not plain strings.
TYPED = {"amount": int, "trial": int, "max": int, "months": int, "percent": float}
FLAGS = {"disabled", "contact", "apply"}
DEFAULTS = {"currency": "usd", "interval": "month", "trial": 0, "max": 0, "months": 0, "percent": 100.0,
            "duration": "forever"}
REQUIRED = {("role:save", "scopes")}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cli", description=__doc__.splitlines()[0])
    root.add_argument("--json", action="store_true", help="Machine-readable output")
    subs = root.add_subparsers(dest="command", required=True, metavar="<command>")

    for name, help_text, handler, positionals, options in COMMANDS:
        sub = subs.add_parser(name, help=help_text)
        for positional in positionals:
            # A trailing "?" makes it optional, e.g. a plan code derived from --name.
            name_, optional = positional.rstrip("?"), positional.endswith("?")
            sub.add_argument(name_, nargs="?" if optional else None, default=None)
        for option in options:
            key = option.replace("-", "_")
            if key in FLAGS:
                sub.add_argument(f"--{option}", action="store_true")
            else:
                sub.add_argument(
                    f"--{option}",
                    type=TYPED.get(key, str),
                    default=DEFAULTS.get(key),
                    required=(name, key) in REQUIRED,
                )
        sub.set_defaults(handler=handler)
    return root


async def run(args: argparse.Namespace) -> None:
    await args.handler(build_context(), Printer(args.json), args)


def main() -> None:
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except (AdminError, PlatformError) as error:
        # A refusal the operator can act on — no traceback, non-zero exit for scripts.
        sys.exit(f"  {error}")


if __name__ == "__main__":
    main()
