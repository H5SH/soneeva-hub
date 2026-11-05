"""Who is calling: the app that signed the token, the business it acts for, and the person if any.

Each loader fills in what its routes need, so a field left at its default wasn't asked for:
`verify` the token (src/services/tokens/caller.py), `ActorResolver` the person's scopes
(src/services/people/access.py), `find_principal` their payment side (src/services/tokens/staff.py).
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from src.models.admin import SUPERADMIN, Scope
from src.models.mode import Mode

if TYPE_CHECKING:
    from src.models.platform import Business


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = ""
    sub: str = "-"
    uid: str | None = None
    mode: Mode = Field(default=Mode.LIVE, description="test when the token's iss is `{appId}_staging`.")
    app_scopes: list[str] = Field(default_factory=list, description="The `hub.*` the app may use, narrowed "
                                  "to what a named person's role allows.")
    scopes: list[str] = Field(default_factory=list, description="The person's scopes where they act: their "
                              "member role or grant in the business, or their platform role.")
    role_scopes: list[str] | None = Field(default=None, description="A platform person's role, raw; None is "
                                          "no platform person.")
    payment_scopes: list[Scope] = Field(default_factory=list, description="That role's `payment.*`.")
    platform: bool = Field(default=False, description="A platform person: has a `users/{uid}` record.")
    email: str = ""
    role: str | None = None
    tokens: int | None = None
    apps: list[str] = Field(default_factory=list, description="Apps this person may act on.")
    businesses: list[str] = Field(default_factory=list, description="Person limit; empty is none.")
    assigned_only: bool = Field(default=False, description="Businesses.Access alone: only theirs.")

    @property
    def business_id(self) -> str | None:
        return None if self.sub == "-" else self.sub

    # ── businesses ───────────────────────────────────────────────────────────
    def assigned_to(self, business: "Business") -> bool:
        return bool(self.uid) and self.uid == business.servicepersonUid

    def reaches_business(self, business: "Business") -> bool:
        """A staging caller only ever reaches test businesses. A platform person reaches a sandbox twin
        as they reach its live business (its serviceperson and business limit are the live one's)."""
        if self.mode is Mode.TEST and not business.test:
            return False
        if self.business_id:
            return self.business_id == business.id
        if self.assigned_only and not business.sandboxOf and not self.assigned_to(business):
            return False
        return not self.businesses or (business.sandboxOf or business.id) in self.businesses

    def inside(self, business_id: str) -> bool:
        """A member of (or a serviceperson with a running grant in) that business, by its token."""
        return self.business_id == business_id and bool(self.scopes)

    def has_exact(self, scope: str) -> bool:
        """Listed as is; a platform person's `*` goes through `rules.holds`."""
        return scope in self.scopes

    # ── payments ─────────────────────────────────────────────────────────────
    @property
    def is_superadmin(self) -> bool:
        return self.role == SUPERADMIN

    @property
    def sees_all(self) -> bool:
        return self.is_superadmin or self.has(Scope.PAYMENT_ADMIN)

    @property
    def all_apps(self) -> bool:
        return self.is_superadmin or "payment.admin" in self.scopes

    def has(self, scope: Scope) -> bool:
        return scope in self.payment_scopes

    def may(self, scope: Scope, app_id: str | None = None) -> bool:
        """Scope, then app membership. Superadmin and payment.admin skip membership."""
        if not self.has(scope):
            return False
        return app_id is None or self.sees_all or app_id in self.apps

    def reaches_id(self, business_id: str, app_id: str | None = None) -> bool:
        """Business limit: an empty `businesses` list means no limit."""
        if self.sees_all:
            return True
        if app_id and app_id not in self.apps:
            return False
        return not self.businesses or business_id in self.businesses
