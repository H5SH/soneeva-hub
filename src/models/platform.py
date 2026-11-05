"""Shapes of the platform API (/v1) and the documents behind it. camelCase, per the contract."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.hours import WeekHours
from src.models.mode import Mode
from src.models.people import USERNAME, OwnerAccount, StoreOwner
from src.models.stores import StorePlan, StoreRef


class BusinessStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    ONHOLD = "onhold"
    #: Soft delete: hidden from listings unless asked for by status; needs Businesses.Delete.
    DELETED = "deleted"


class BusinessCreate(BaseModel):
    """Sign-up. `owner` creates the owner's account here; `ownerUid` names an existing one (the console's
    Google sign-up), refused with 403 when it is a platform person (`users/{uid}` in `(default)`). A sign-up
    from a store names its `store` and gives an `owner` without a password; any other needs a password."""

    ownerUid: str | None = Field(default=None, min_length=1)
    ownerUsername: str | None = Field(default=None, pattern=USERNAME)
    owner: OwnerAccount | StoreOwner | None = None
    store: StoreRef | None = None
    name: str = Field(min_length=1, max_length=120)
    type: str
    country: str = Field(min_length=2, max_length=2)
    timezone: str
    languages: list[str] = Field(default_factory=list)
    branchCount: int = Field(default=1, ge=1, le=500)
    phone: str = ""
    email: str = ""
    address: str = ""
    hours: WeekHours = Field(default_factory=WeekHours)
    consentAt: str = ""
    appId: str | None = None
    dataRegion: str | None = Field(default=None, pattern=r"^[a-z]{2,8}$",
                                   description="A shared region (GET /v1/data-locations); none: by country.")
    dataLocation: str | None = Field(default=None, pattern=r"^[a-z0-9-]{2,40}$",
                                     description="A Firestore location for a database of its own, billed "
                                     "separately; wins over dataRegion.")

    @model_validator(mode="after")
    def _one_owner(self) -> "BusinessCreate":
        if bool(self.ownerUid) == bool(self.owner):
            raise ValueError("Give exactly one of owner and ownerUid")
        passwordless = self.owner is not None and not isinstance(self.owner, OwnerAccount)
        if bool(self.store) != passwordless:
            raise ValueError("A store's owner has no password, and every other owner needs one")
        return self


class BusinessPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BusinessStatus | None = None
    configured: bool | None = None
    servicepersonUid: str | None = None
    voiceUrl: str | None = Field(default=None, max_length=500, pattern=r"^https://[^\s/]+(/[^\s]*)?$",
                                 description="Where the developer deployed its voice; needs Voice.Url.Write.")


class ChecklistTick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    done: bool


class BusinessPlan(BaseModel):
    """Never stored: from the Stripe subscription, `active` while it grants access."""

    code: str | None = None
    status: str = "none"
    active: bool = False


class PhoneNumber(BaseModel):
    phoneNumber: str
    sid: str = ""
    at: str = ""


class Uninstalled(BaseModel):
    """Its store uninstalled the app: the status a reinstall gives back, and when (for the retention job)."""

    status: BusinessStatus | None = Field(description="None: also deleted on the platform.")
    at: datetime


class Business(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    type: str
    appId: str
    dbId: str = Field(default="", description="Where its data is (src/db/place.py): none, `{region}/{id}` "
                      "or a database of its own.")
    dataLocation: str = Field(default="", description="The Firestore location of a database of its own.")
    ownerUid: str
    status: BusinessStatus = BusinessStatus.PENDING
    configured: bool = False
    provisioned: bool = False
    provisionError: str | None = Field(default=None, description="Why provisioning last failed; retry it.")
    provisionStartedAt: datetime | None = Field(default=None, exclude=True)
    servicepersonUid: str | None = None
    numbers: list[PhoneNumber] = Field(default_factory=list)
    voiceUrl: str | None = None
    plan: BusinessPlan | None = None
    test: bool = Field(default=False, description="Signed up by a staging caller: Stripe test mode for good.")
    country: str = ""
    timezone: str = ""
    languages: list[str] = Field(default_factory=list)
    branchCount: int = 1
    phone: str = ""
    email: str = ""
    twilioAccountSid: str | None = None
    consentAt: str = ""
    createdAt: Any = None
    sandboxOf: str | None = Field(default=None, description="A sandbox twin: the live business it tests.")
    sandboxId: str | None = Field(default=None, description="This business's sandbox twin, once made.")
    uninstalled: Uninstalled | None = Field(default=None, exclude=True)
    reenabledAt: datetime | None = Field(default=None, exclude=True, description="The last reinstall.")
    chargeFailedAt: datetime | None = Field(default=None, exclude=True, description="Store charges failing "
                                            "since then: past `stores.chargeGraceSeconds`, service stops.")
    storePlan: StorePlan | None = Field(default=None, exclude=True, description="Paid through Shopify: wins "
                                        "over Stripe; the API shows it as `plan`.")

    @property
    def mode(self) -> Mode:
        return Mode.TEST if self.test else Mode.LIVE


class Serviceperson(BaseModel):
    model_config = ConfigDict(extra="ignore")

    uid: str
    name: str
    phone: str = ""
    email: str = ""
    active: bool = True


class UsageReport(BaseModel):
    callId: str = Field(min_length=1, max_length=128)
    seconds: int = Field(ge=0)


class NumberPurchase(BaseModel):
    phoneNumber: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
