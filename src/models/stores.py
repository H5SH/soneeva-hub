"""Shopify and BigCommerce stores, and a plan paid through one (src/services/stores.py)."""

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.models.db import store_id
from src.models.subscription import SubscriptionStatus


class StorePlatform(StrEnum):
    SHOPIFY = "shopify"
    BIGCOMMERCE = "bigcommerce"


#: A shop domain (`rosa.myshopify.com`) or a store hash (`abc123`).
STORE_ID = r"^[a-z0-9][a-z0-9.-]{0,254}$"


class StoreRef(BaseModel):
    """The console checked what the platform sent before it names a store here, so it is trusted."""

    model_config = ConfigDict(extra="forbid")

    platform: StorePlatform
    storeId: str = Field(pattern=STORE_ID)

    def doc(self, test: bool) -> str:
        return store_id(self.platform, self.storeId, test)


class StoreEvent(BaseModel):
    """When the platform says something happened, so a late retry can be told from news."""

    model_config = ConfigDict(extra="forbid")

    at: AwareDatetime


class StorePlanStatus(StrEnum):
    """Shopify's AppSubscriptionStatus."""

    ACTIVE = "active"
    PENDING = "pending"
    CANCELLED = "cancelled"
    DECLINED = "declined"
    FROZEN = "frozen"
    EXPIRED = "expired"


STORE_PLAN_STATUS = {
    StorePlanStatus.ACTIVE: SubscriptionStatus.ACTIVE,
    StorePlanStatus.PENDING: SubscriptionStatus.INCOMPLETE,
    StorePlanStatus.DECLINED: SubscriptionStatus.CANCELED,
    StorePlanStatus.CANCELLED: SubscriptionStatus.CANCELED,
    StorePlanStatus.FROZEN: SubscriptionStatus.PAUSED,
    StorePlanStatus.EXPIRED: SubscriptionStatus.CANCELED,
}


class StorePlan(BaseModel):
    """A plan paid through Shopify instead of Stripe, as last heard. Stored, unlike Stripe's."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    source: Literal[StorePlatform.SHOPIFY]
    subscriptionId: str = Field(min_length=1, max_length=200)
    usageLineItemId: str | None = Field(default=None, min_length=1, max_length=200, description="What usage "
                                        "is charged to. Needed to go active; kept for the same subscription.")
    status: StorePlanStatus
    at: AwareDatetime = Field(description="When Shopify changed it (`updated_at`): older news is dropped.")

    @property
    def is_active(self) -> bool:
        return self.status is StorePlanStatus.ACTIVE
