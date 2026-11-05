"""Everything routes depend on, in one place. Every caller is a machine holding an Ed25519 key."""

from dataclasses import fields
from typing import Annotated, NamedTuple

from fastapi import Depends, HTTPException, Path, Request, status

from src.config import AppRegistry, Settings
from src.db import Database
from src.models.app import AppConfig
from src.models.mode import Mode, staged
from src.reader import Reader
from src.services.admin import AdminService
from src.services.advertisements import AdvertisementService
from src.services.agent_tokens import AgentTokenService
from src.services.billing import BillingService
from src.services.businesses import BusinessService
from src.services.callers import CallerService
from src.services.coupons import CouponService
from src.services.customers import CustomerService
from src.services.handoff import HandoffService
from src.services.ivr import IvrService
from src.services.media import MediaRules
from src.services.modes import ByMode
from src.services.people.members import MemberService
from src.services.people.roles import RoleService
from src.services.people.service import PeopleService
from src.services.phone_numbers import PhoneNumberService
from src.services.plans import PlanService
from src.services.platform.data import PlatformData
from src.services.platform.home import HomeService
from src.services.sandbox import SandboxService
from src.services.stores import StoreService
from src.services.tokens.deps import ApiAuthDep
from src.services.usage import UsageService
from src.services.voice import VoiceService
from src.services.webhook import WebhookService
from src.storage import FileStore
from src.wiring import Services

# ── wiring ───────────────────────────────────────────────────────────────────


def _state(name: str):
    """That one of the `Services`; a name it doesn't have stops the import, not a request."""
    if name not in {f.name for f in fields(Services)}:
        raise ValueError(f"No service called '{name}'")

    def get(request: Request):
        return getattr(request.app.state, name)

    return get


class AppRef(NamedTuple):
    """The path's app id without `_staging`, and the Stripe mode the suffix selects."""

    id: str
    mode: Mode


def get_ref(app_id: Annotated[str, Path(description="An app id; `{appId}_staging` is test mode")]) -> AppRef:
    return AppRef(*staged(app_id))


AppRefDep = Annotated[AppRef, Depends(get_ref)]


def _for_mode(name: str):
    """That service in the path app's mode."""

    def get(request: Request, ref: AppRefDep):
        return getattr(request.app.state, name)[ref.mode]

    return get


async def get_app(ref: AppRefDep, registry: AppRegistry = Depends(_state("registry"))) -> AppConfig:
    app = await registry.get(ref.id, ref.mode)
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown app '{ref.id}'")
    return app


SettingsDep = Annotated[Settings, Depends(_state("settings"))]
RegistryDep = Annotated[AppRegistry, Depends(_state("registry"))]
AppDep = Annotated[AppConfig, Depends(get_app)]
AdminDep = Annotated[AdminService, Depends(_state("admin"))]
# In the path app's mode:
BillingDep = Annotated[BillingService, Depends(_for_mode("billing"))]
CouponDep = Annotated[CouponService, Depends(_for_mode("coupons"))]
PlanDep = Annotated[PlanService, Depends(_for_mode("plans"))]
# Every mode, for routes that pick one from a business or a query:
BillingsDep = Annotated[ByMode[BillingService], Depends(_state("billing"))]
PlansDep = Annotated[ByMode[PlanService], Depends(_state("plans"))]
WebhooksDep = Annotated[ByMode[WebhookService], Depends(_state("webhooks"))]
BusinessDep = Annotated[BusinessService, Depends(_state("businesses"))]
DbDep = Annotated[Database, Depends(_state("db"))]
HandoffDep = Annotated[HandoffService, Depends(_state("handoff"))]
AgentTokensDep = Annotated[AgentTokenService, Depends(_state("agent_tokens"))]
StoresDep = Annotated[StoreService, Depends(_state("stores"))]
VoiceDep = Annotated[VoiceService, Depends(_state("voice"))]
SandboxDep = Annotated[SandboxService, Depends(_state("sandbox"))]
UsageDep = Annotated[UsageService, Depends(_state("usage"))]
NumbersDep = Annotated[PhoneNumberService, Depends(_state("numbers"))]
CustomerDep = Annotated[CustomerService, Depends(_state("customers"))]
PeopleDep = Annotated[PeopleService, Depends(_state("people"))]
MembersDep = Annotated[MemberService, Depends(_state("members"))]
RolesDep = Annotated[RoleService, Depends(_state("role_service"))]
PlatformDataDep = Annotated[PlatformData, Depends(_state("platform_data"))]
HomeDep = Annotated[HomeService, Depends(_state("home"))]
MediaDep = Annotated[MediaRules, Depends(_state("media"))]
StorageDep = Annotated[FileStore, Depends(_state("storage"))]
ReaderDep = Annotated[Reader, Depends(_state("reader"))]
IvrDep = Annotated[IvrService, Depends(_state("ivr"))]
CallersDep = Annotated[CallerService, Depends(_state("callers"))]
AdvertisementDep = Annotated[AdvertisementService, Depends(_state("advertisements"))]

# Machine callers. Declared on the router, not per route — every /v1 route needs one.
AuthDep = ApiAuthDep
