"""Platform people acting through karbon-admin on /v1/payment and /v1/me."""

from src.models.admin import Scope

ADMIN_APP = [Scope.HUB_PAYMENT_VIEW, Scope.HUB_PAYMENT_UPDATE, Scope.HUB_HANDOFF_CREATE,
             Scope.HUB_PEOPLE_VIEW]


def staff(services, bearer, uid: str = "op", role: str = "superadmin", scopes: list[Scope] | None = None,
          apps: list[str] | None = None, businesses: list[str] | None = None):
    """Saves `users/{uid}` (and `roles/{role}` when scopes are given); returns a header maker."""
    services.accounts.add(uid, username=uid)
    services.db.rows[("users", uid)] = {"uid": uid, "email": f"{uid}@example.com", "role": role,
                                        "apps": apps or [], "businesses": businesses or []}
    if scopes is not None:
        services.db.rows[("roles", role)] = {"scopes": [str(s) for s in scopes]}
    return lambda: bearer("karbon-admin", ADMIN_APP, uid=uid)
