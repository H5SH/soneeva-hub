"""Accounts (/v1/accounts), platform people (/v1/people) and roles (/v1/roles). camelCase."""

from pydantic import BaseModel, ConfigDict, Field

USERNAME = r"^[A-Za-z0-9._-]{3,32}$"
PHONE = r"^(\+?[0-9][0-9 ()-]{4,30})?$"
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Account(BaseModel):
    """A Firebase Auth account. `businessId` and `username` come from its custom claims."""

    uid: str
    email: str = ""
    displayName: str = ""
    disabled: bool = False
    username: str | None = None
    businessId: str | None = None
    createdAt: str | None = None
    lastSignInAt: str | None = None


class Person(Account):
    """A platform person: the account plus `(default)/users/{uid}`. `tokens` None is no limit; empty
    `businesses` is every business; `apps` are the payment apps they act on. `phone`: a contact number
    (a serviceperson's, shown to the businesses they look after)."""

    role: str | None = None
    phone: str = ""
    tokens: int | None = None
    servicepersons: list[str] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)
    businesses: list[str] = Field(default_factory=list)


class StoreOwner(BaseModel):
    """Signing up from a store: the store vouches for them, so no password ("forgot password" sets one)."""

    email: str = Field(pattern=EMAIL, max_length=200)
    displayName: str = Field(min_length=1, max_length=120)
    username: str = Field(pattern=USERNAME)


class OwnerAccount(StoreOwner):
    password: str = Field(min_length=6, max_length=200)


class AccountCreate(OwnerAccount):
    """A member of the calling business (a business token only)."""

    businessId: str | None = None


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, pattern=EMAIL, max_length=200)
    password: str | None = Field(default=None, min_length=6, max_length=200)
    displayName: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, pattern=USERNAME)
    disabled: bool | None = None


class PersonCreate(BaseModel):
    """No `password`: a random one, and a link to set their own is returned once."""

    email: str = Field(pattern=EMAIL, max_length=200)
    displayName: str = Field(min_length=1, max_length=120)
    username: str = Field(pattern=USERNAME)
    password: str | None = Field(default=None, min_length=6, max_length=200)
    phone: str = Field(default="", pattern=PHONE)
    role: str | None = None
    servicepersons: list[str] | None = Field(default=None, max_length=200)
    tokens: int | None = Field(default=None, ge=0)
    apps: list[str] = Field(default_factory=list, max_length=100)
    businesses: list[str] = Field(default_factory=list, max_length=500)


class PersonPatch(AccountPatch):
    phone: str | None = Field(default=None, pattern=PHONE)
    role: str | None = None
    apps: list[str] | None = Field(default=None, max_length=100)
    businesses: list[str] | None = Field(default=None, max_length=500)


class ResetRequest(BaseModel):
    send: bool = False


class RecoverRequest(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=200)


class TokensPut(BaseModel):
    tokens: int | None = Field(ge=0)


class AssignPut(BaseModel):
    servicepersons: list[str] = Field(max_length=200)


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] = Field(default_factory=list, max_length=200)


class RolePut(BaseModel):
    scopes: list[str] = Field(max_length=200)
