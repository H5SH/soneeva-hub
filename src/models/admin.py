from enum import StrEnum

from pydantic import BaseModel, Field

from src.models.db import STORED


class Scope(StrEnum):
    """karbon-hub's own scope catalogue. `payment.*` and `platform.*` are person scopes (a role holds
    them next to console ids such as `Users.View`), `hub.*` are app scopes on the /v1 API."""

    PAYMENT_ADMIN = "payment.admin"

    APP_VIEW = "payment.app.view"
    APP_CREATE = "payment.app.create"
    APP_UPDATE = "payment.app.update"
    APP_DELETE = "payment.app.delete"

    PLAN_VIEW = "payment.plan.view"
    PLAN_CREATE = "payment.plan.create"
    PLAN_UPDATE = "payment.plan.update"
    PLAN_DELETE = "payment.plan.delete"

    COUPON_VIEW = "payment.coupon.view"
    COUPON_CREATE = "payment.coupon.create"
    COUPON_UPDATE = "payment.coupon.update"
    COUPON_DELETE = "payment.coupon.delete"

    KEY_ROTATE = "payment.key.rotate"

    BUSINESS_VIEW = "payment.business.view"
    INVOICE_VIEW = "payment.invoice.view"
    USAGE_VIEW = "payment.usage.view"

    HUB_BUSINESS_CREATE = "hub.business.create"
    HUB_BUSINESS_VIEW = "hub.business.view"
    HUB_BUSINESS_UPDATE = "hub.business.update"
    HUB_SERVICEPERSON_VIEW = "hub.serviceperson.view"
    HUB_VOICE_UPDATE = "hub.voice.update"
    HUB_NUMBER_VIEW = "hub.number.view"
    HUB_NUMBER_BUY = "hub.number.buy"
    HUB_USAGE_WRITE = "hub.usage.write"
    HUB_PLAN_VIEW = "hub.plan.view"
    HUB_ACCOUNT_VIEW = "hub.account.view"
    HUB_ACCOUNT_CREATE = "hub.account.create"
    HUB_ACCOUNT_UPDATE = "hub.account.update"
    HUB_ACCOUNT_DELETE = "hub.account.delete"
    HUB_ACCOUNT_RECOVER = "hub.account.recover"
    HUB_MEDIA_VIEW = "hub.media.view"
    HUB_MEDIA_UPDATE = "hub.media.update"
    HUB_KNOWLEDGE_READ = "hub.knowledge.read"
    HUB_PLATFORM_VIEW = "hub.platform.view"
    HUB_DEMO_VIEW = "hub.demo.view"
    HUB_DEMO_UPDATE = "hub.demo.update"
    HUB_PEOPLE_VIEW = "hub.people.view"
    HUB_PEOPLE_UPDATE = "hub.people.update"
    HUB_PAYMENT_VIEW = "hub.payment.view"
    HUB_PAYMENT_UPDATE = "hub.payment.update"
    HUB_HANDOFF_CREATE = "hub.handoff.create"
    HUB_HANDOFF_REDEEM = "hub.handoff.redeem"
    HUB_BILLING_VIEW = "hub.billing.view"
    HUB_BILLING_UPDATE = "hub.billing.update"
    HUB_ADVERTISEMENT_VIEW = "hub.advertisement.view"
    HUB_ADVERTISEMENT_UPDATE = "hub.advertisement.update"
    HUB_STORE_SIGN_IN = "hub.store.signin"
    HUB_STORE_UPDATE = "hub.store.update"

    PLATFORM_DOCS_VIEW = "platform.docs.view"

    @property
    def group(self) -> str:
        return self.value.split(".")[1]


SUPERADMIN = "superadmin"
SERVICEPERSON = "serviceperson"
SALES = "sales"

#: App scopes: what a calling app or tool may do on /v1. Never held by a person's role.
APP_SCOPE = "hub."
#: Person scopes a role may hold next to the console ids (`platformScopes` in settings.json).
ROLE_SCOPES: list[Scope] = [s for s in Scope if not s.value.startswith(APP_SCOPE)]


#: The payment side of the built-in roles; `platformRoles` (settings.json) adds their console ids.
DEFAULT_ROLES: dict[str, list[Scope]] = {
    SUPERADMIN: ROLE_SCOPES,
    SERVICEPERSON: [
        Scope.APP_VIEW, Scope.PLAN_VIEW, Scope.COUPON_VIEW, Scope.COUPON_CREATE,
        Scope.BUSINESS_VIEW, Scope.INVOICE_VIEW, Scope.USAGE_VIEW,
    ],
    SALES: [Scope.APP_VIEW, Scope.PLAN_VIEW, Scope.BUSINESS_VIEW],
}


class UserRecord(BaseModel):
    """A platform person: `(default)/users/{uid}`."""

    model_config = STORED

    uid: str
    email: str = ""
    role: str = ""
    apps: list[str] = Field(default_factory=list, description="Apps this person may act on.")
    businesses: list[str] = Field(default_factory=list, description="Empty means every business.")
    disabled: bool = False
