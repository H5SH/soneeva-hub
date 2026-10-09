"""Everything the dashboard does, from a terminal.

    uv run python -m cli --help

Commands call the same services the routes call, so the two cannot drift. Add a command here
only once its behaviour lives in a service.
"""

import argparse
import asyncio
import sys

from app.services.admin import AdminError
from cli import apps, people
from cli.context import Context
from cli.output import Printer

# name, help, handler, positionals, options
COMMANDS = [
    ("apps", "List apps", apps.list_apps, [], []),
    ("app:show", "Show one app in full", apps.show_app, ["app_id"], []),
    ("app:create", "Create an app", apps.create_app, ["app_id"],
     ["name", "return-url", "hosts"]),
    ("app:update", "Change an app's fields", apps.update_app, ["app_id"],
     ["name", "return-url", "hosts", "free-plan"]),
    ("app:delete", "Delete an app", apps.delete_app, ["app_id"], []),
    ("key:rotate", "Issue a key and print the env block", apps.rotate_key, ["app_id"], []),
    ("plan:save", "Create or update a plan", apps.save_plan, ["app_id", "code"],
     ["name", "amount", "currency", "interval", "trial", "features"]),
    ("plan:delete", "Remove a plan", apps.delete_plan, ["app_id", "code"], []),
    ("coupons", "List an app's coupons", apps.list_coupons, ["app_id"], []),
    ("coupon:create", "Create a 100%%-off code", apps.create_coupon, ["app_id"],
     ["code", "max"]),
    ("coupon:delete", "Deactivate a code", apps.delete_coupon, ["app_id", "code"], []),
    ("subscription", "Entitlements as the consuming app sees them",
     apps.show_subscription, ["app_id", "tenant"], []),
    ("users", "List people", people.list_users, [], []),
    ("user:create", "Create an account, print a password-set link", people.create_user,
     ["email"], []),
    ("user:save", "Set someone's role and apps", people.save_user, ["uid"],
     ["role", "apps", "disabled"]),
    ("user:delete", "Remove an account", people.delete_user, ["uid"], []),
    ("roles", "List roles", people.list_roles, [], []),
    ("role:show", "Show a role's scopes", people.show_role, ["role"], []),
    ("role:save", "Create or update a role ('all' for every scope)", people.save_role,
     ["role"], ["scopes"]),
    ("role:delete", "Remove a role", people.delete_role, ["role"], []),
    ("scopes", "List every scope that exists", people.list_scopes, [], []),
]

# Options that are not plain strings.
TYPED = {"amount": int, "trial": int, "max": int}
FLAGS = {"disabled"}
DEFAULTS = {"currency": "usd", "interval": "month", "amount": 0, "trial": 0, "max": 0}
REQUIRED = {"scopes"}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cli", description=__doc__.splitlines()[0])
    root.add_argument("--json", action="store_true", help="Machine-readable output")
    subs = root.add_subparsers(dest="command", required=True, metavar="<command>")

    for name, help_text, handler, positionals, options in COMMANDS:
        sub = subs.add_parser(name, help=help_text)
        for positional in positionals:
            sub.add_argument(positional)
        for option in options:
            key = option.replace("-", "_")
            if key in FLAGS:
                sub.add_argument(f"--{option}", action="store_true")
            else:
                sub.add_argument(
                    f"--{option}",
                    type=TYPED.get(key, str),
                    default=DEFAULTS.get(key),
                    required=key in REQUIRED,
                )
        sub.set_defaults(handler=handler)
    return root


async def run(args: argparse.Namespace) -> None:
    await args.handler(Context.build(), Printer(args.json), args)


def main() -> None:
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except AdminError as error:
        # A refusal the operator can act on — no traceback, non-zero exit for scripts.
        sys.exit(f"  {error}")


if __name__ == "__main__":
    main()
