"""The console's platform scope rules, enforced again here. Pure functions.

A role holds console scope ids (`Users.View`); `Assigned.Users.View` reaches only the users the
person looks after, and themselves. `*` is every console scope.
"""

from collections.abc import Iterable
from typing import Literal

from src.models.admin import ROLE_SCOPES, Scope
from src.models.platform import Business, Caller
from src.services.errors import PlatformError
from src.tunables import tunable

ASSIGNED = "Assigned."
#: Editing your own name, password and avatar: platform role / business role.
PROFILE_WRITE, PROFILE_EDIT = "Profile.Write", "profile.edit"
Reach = Literal["all", "assigned"] | None
HUB = {str(s) for s in Scope}


def holds(scopes: Iterable[str], scope: str) -> bool:
    scopes = set(scopes)
    return scope in scopes or ("*" in scopes and scope not in HUB)


def reach(scopes: Iterable[str], scope: str) -> Reach:
    scopes = list(scopes)
    if holds(scopes, scope):
        return "all"
    return "assigned" if holds(scopes, ASSIGNED + scope) else None


def business_reach(scopes: Iterable[str]) -> Reach:
    """Businesses.View or .Assign reach every business; Businesses.Access alone only the person's own."""
    scopes = list(scopes)
    if holds(scopes, "Businesses.View") or holds(scopes, "Businesses.Assign"):
        return "all"
    return "assigned" if holds(scopes, "Businesses.Access") else None


CANNOT_READ = "Requires Businesses.View, or Businesses.Access on a business assigned to you"


def reads_business(caller: Caller, business: Business) -> bool:
    """A business's details (branches, checklist, callers, call menus): Businesses.View for every
    business, Businesses.Access for those assigned to the person. Not a person: checked elsewhere."""
    role = caller.person_scopes
    if role is None:
        return True
    assigned = caller.assigned_to(business) and holds(role, "Businesses.Access")
    return assigned or holds(role, "Businesses.View")


def covers(scopes: Iterable[str], scope: str) -> bool:
    """Holds the scope, or the global twin of an `Assigned.` one."""
    scopes = list(scopes)
    if holds(scopes, scope):
        return True
    return scope.startswith(ASSIGNED) and holds(scopes, scope[len(ASSIGNED):])


def catalogue() -> list[dict]:
    """Every scope a role may hold: the console ids (`platformScopes`; `twin`: an `Assigned.` twin
    exists), then `payment.*` and `platform.*`. `*` stands for every console id."""
    console = [{"id": s, "group": g["group"], "twin": g["twins"]} for g in tunable("platformScopes")
               for s in g["scopes"]]
    group = {"payment": "payments", "platform": "platform"}
    return console + [{"id": str(s), "group": group[s.value.split(".")[0]], "twin": False}
                      for s in ROLE_SCOPES]


def known() -> set[str]:
    entries = catalogue()
    return {"*"} | {e["id"] for e in entries} | {ASSIGNED + e["id"] for e in entries if e["twin"]}


def assignable(scopes: list[str], roles: dict[str, list[str]]) -> list[str]:
    """Roles whose every scope the person holds; ids no longer in the catalogue are ignored."""
    ids = known()
    return [name for name, held in roles.items() if all(covers(scopes, s) for s in held if s in ids)]


def is_serviceperson_role(scopes: list[str]) -> bool:
    """As the console reads it: `*` expands to every scope, `Assigned.` ones included."""
    return "*" in scopes or any(s.startswith(ASSIGNED) for s in scopes)


def held(tokens: int | None) -> int:
    """What a user holds that came out of someone's balance. Overspent is nothing."""
    return max(0, tokens or 0)


class Refused(PlatformError):
    """A rule said no; the message goes back to the caller (403 unless said otherwise)."""

    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message, status)


def plan_tokens(scope_reach: Reach, self_target: bool, nxt: int | None):
    """The transfer plan for a token change, run inside the transaction (People.transfer)."""

    def plan(current: int | None, balance: int | None) -> int:
        if nxt == current or scope_reach == "all":
            return 0
        if self_target:
            raise Refused("You can't change your own tokens")
        if balance is None:
            return 0
        if nxt is None:
            raise Refused("Only someone with no limit can give no limit")
        cost = nxt - held(current)
        if cost > held(balance):
            raise Refused(f"You have {held(balance)} tokens left to give", 409)
        return -cost

    return plan
