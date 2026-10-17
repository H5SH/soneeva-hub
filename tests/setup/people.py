"""Platform people for the /v1/accounts, /v1/people, /v1/roles and /v1/platform tests."""

from src.models.admin import ROLE_SCOPES, Scope

APP = [
    Scope.HUB_ACCOUNT_VIEW,
    Scope.HUB_ACCOUNT_CREATE,
    Scope.HUB_ACCOUNT_UPDATE,
    Scope.HUB_ACCOUNT_DELETE,
    Scope.HUB_PEOPLE_VIEW,
    Scope.HUB_PEOPLE_UPDATE,
    Scope.HUB_PLATFORM_VIEW,
    Scope.HUB_BUSINESS_CREATE,
]

ROLES = {
    "superadmin": ["*", *[str(s) for s in ROLE_SCOPES]],
    "serviceperson": [
        "Assigned.Users.View",
        "Assigned.Users.Create",
        "Assigned.Users.Tokens.Write",
        "Assigned.Users.Delete",
        "Assigned.Users.ResetPassword",
        "Assigned.Chats.View",
        "hub.business.view",  # a stale id from before roles held only person scopes: ignored
    ],
    "user": [],
}


def people(services) -> None:
    """admin (superadmin), sp (serviceperson, 100 tokens) looking after u1, u2 unassigned, biz member."""
    rows, accounts = services.db.rows, services.accounts
    for name, scopes in ROLES.items():
        rows[("roles", name)] = {"scopes": list(scopes)}
    for uid, role in (("admin", "superadmin"), ("sp", "serviceperson"), ("u1", "user"), ("u2", "user")):
        accounts.add(uid, username=uid)
        rows[("users", uid)] = {"role": role}
    rows[("users", "sp")]["tokens"] = 100
    rows[("users", "u1")]["tokens"] = 30
    rows[("assignments", "sp_u1")] = {"servicepersonUid": "sp", "userUid": "u1"}
    accounts.add("member", username="member", businessId="biz1")


def as_(bearer, uid: str, sub: str = "-", scopes: list[Scope] | None = None) -> dict[str, str]:
    return bearer("soneeva", scopes if scopes is not None else APP, sub=sub, uid=uid)


def assignments(services) -> set[tuple[str, str]]:
    rows = services.db.rows.items()
    return {(r["servicepersonUid"], r["userUid"]) for (c, _), r in rows if c == "assignments"}
