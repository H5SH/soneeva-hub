from enum import StrEnum

from pydantic import BaseModel, Field


class Scope(StrEnum):
    """What an action is. Which app it may touch is a separate check — see Principal.may."""

    APP_VIEW = "App.View"
    APP_CREATE = "App.Create"
    APP_UPDATE = "App.Update"
    APP_DELETE = "App.Delete"

    PLAN_VIEW = "Plan.View"
    PLAN_CREATE = "Plan.Create"
    PLAN_UPDATE = "Plan.Update"
    PLAN_DELETE = "Plan.Delete"

    COUPON_VIEW = "Coupon.View"
    COUPON_CREATE = "Coupon.Create"
    COUPON_UPDATE = "Coupon.Update"
    COUPON_DELETE = "Coupon.Delete"

    KEY_ROTATE = "Key.Rotate"

    USER_VIEW = "User.View"
    USER_INVITE = "User.Invite"
    USER_UPDATE = "User.Update"
    USER_DELETE = "User.Delete"


SUPERADMIN = "superadmin"
SERVICEPERSON = "serviceperson"

#: Seeded on first boot. Editable afterwards in the dashboard, which is why they live in the
#: database rather than here.
DEFAULT_ROLES: dict[str, list[Scope]] = {
    SUPERADMIN: list(Scope),
    SERVICEPERSON: [
        Scope.APP_VIEW,
        Scope.KEY_ROTATE,
        Scope.PLAN_VIEW,
        Scope.COUPON_VIEW,
        Scope.COUPON_CREATE,
    ],
}


class UserRecord(BaseModel):
    """A person who can sign in to the dashboard."""

    uid: str
    email: str
    role: str = SERVICEPERSON
    apps: list[str] = Field(default_factory=list, description="Apps this user may act on.")
    disabled: bool = False


class Coupon(BaseModel):
    """A 100%-off promotion code, owned by one app.

    Two bindings, and both are needed: this row is what the scope check reads, and Stripe's
    `applies_to` is what actually stops the code working on another app's checkout.
    """

    code: str
    app_id: str
    stripe_coupon_id: str
    stripe_promotion_code_id: str
    created_by: str
    max_redemptions: int | None = None
    active: bool = True


class Principal(BaseModel):
    """A verified signed-in user, with their role's scopes already resolved."""

    uid: str
    email: str
    role: str
    scopes: list[Scope] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)

    @property
    def is_superadmin(self) -> bool:
        return self.role == SUPERADMIN

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    def may(self, scope: Scope, app_id: str | None = None) -> bool:
        """Scope *and* membership.

        A serviceperson holding Key.Rotate must still belong to the app, or one could rotate
        another app's key. Superadmin skips the membership half, never the scope half.
        """
        if not self.has(scope):
            return False
        if app_id is None or self.is_superadmin:
            return True
        return app_id in self.apps
