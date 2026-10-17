"""Shapes of the platform API (/v1) and the documents behind it. camelCase, per the contract."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.mode import Mode
from src.models.people import USERNAME, OwnerAccount


class BusinessStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    ONHOLD = "onhold"
    #: Soft delete: hidden from listings unless asked for by status; needs Businesses.Delete.
    DELETED = "deleted"


class Caller(BaseModel):
    """A verified /v1 caller: the app, the business it acts for, and the person if any."""

    app_id: str
    sub: str
    uid: str | None = None
    mode: Mode = Field(default=Mode.LIVE, description="test when the token's iss is `{appId}_staging`.")
    scopes: list[str] = Field(default_factory=list)
    businesses: list[str] = Field(default_factory=list, description="Person limit; empty is none.")
    person_scopes: list[str] | None = Field(default=None, description="The platform person's role, raw.")
    assigned_only: bool = Field(default=False, description="Businesses.Access alone: only theirs.")

    @property
    def business_id(self) -> str | None:
        return None if self.sub == "-" else self.sub

    def assigned_to(self, business: "Business") -> bool:
        return bool(self.uid) and self.uid == business.servicepersonUid

    def reaches(self, business: "Business") -> bool:
        """A staging caller only ever reaches test businesses."""
        if self.mode is Mode.TEST and not business.test:
            return False
        if self.business_id:
            return self.business_id == business.id
        if self.assigned_only and not self.assigned_to(business):
            return False
        return not self.businesses or business.id in self.businesses


HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class TimeRange(BaseModel):
    """Open from `open` to `close`, 24h; a close before the open runs past midnight."""

    model_config = ConfigDict(extra="forbid")

    open: str = Field(pattern=HHMM)
    close: str = Field(pattern=HHMM)

    @model_validator(mode="after")
    def _not_empty(self) -> "TimeRange":
        if self.open == self.close:
            raise ValueError("A range opens and closes at different times")
        return self


Day = list[TimeRange]
DayField = Field(default_factory=list, max_length=2)


class WeekHours(BaseModel):
    """A branch's opening hours (`branches/{id}.hours`): per day up to two ranges; empty is closed."""

    model_config = ConfigDict(extra="forbid")

    mon: Day = DayField
    tue: Day = DayField
    wed: Day = DayField
    thu: Day = DayField
    fri: Day = DayField
    sat: Day = DayField
    sun: Day = DayField


class BusinessCreate(BaseModel):
    """Sign-up. `owner` creates the owner's account here; `ownerUid` names an existing one (the console's
    Google sign-up), refused with 403 when it is a platform person (`users/{uid}` in `(default)`)."""

    ownerUid: str | None = Field(default=None, min_length=1)
    ownerUsername: str | None = Field(default=None, pattern=USERNAME)
    owner: OwnerAccount | None = None
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

    @model_validator(mode="after")
    def _one_owner(self) -> "BusinessCreate":
        if bool(self.ownerUid) == bool(self.owner):
            raise ValueError("Give exactly one of owner and ownerUid")
        return self


class BusinessPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BusinessStatus | None = None
    configured: bool | None = None
    servicepersonUid: str | None = None
    mcpUrl: str = Field(default="", max_length=500, pattern=r"^(https://[^\s]+)?$")
    whatsappFrom: str = Field(default="", pattern=r"^(whatsapp:\+[1-9]\d{6,14})?$")


#: Per-business values the hub passes into that business's voice deployment.
AGENT_CONNECTION = ("mcpUrl", "whatsappFrom")


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


class Business(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    type: str
    appId: str
    dbId: str
    ownerUid: str
    status: BusinessStatus = BusinessStatus.PENDING
    configured: bool = False
    provisioned: bool = False
    provisionError: str | None = Field(default=None, description="Why provisioning last failed; retry it.")
    servicepersonUid: str | None = None
    numbers: list[PhoneNumber] = Field(default_factory=list)
    voiceUrl: str | None = None
    mcpUrl: str = ""
    whatsappFrom: str = ""
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


class VoiceSettings(BaseModel):
    language: str = Field(pattern=r"^(auto|[a-z]{2,3}(-[A-Za-z0-9]{2,8})*)$")
    voice: str = Field(min_length=1, max_length=80)


class UsageReport(BaseModel):
    callId: str = Field(min_length=1, max_length=128)
    seconds: int = Field(ge=0)


class NumberPurchase(BaseModel):
    phoneNumber: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
