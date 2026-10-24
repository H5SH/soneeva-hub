"""Builds every service once. Shared by main.py and the CLI so the two cannot drift."""

from dataclasses import dataclass

from src.auth import AccountProvider, Accounts
from src.auth.password import PasswordSignIn
from src.cloud import project_id, region
from src.config import AppRegistry, Settings
from src.db import DB, Database
from src.numbers import NumberProvider, Numbers
from src.payment import Payment, PaymentGateway
from src.remote import Remote, RemoteService
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
from src.services.people.access import ActorResolver
from src.services.people.members import MemberService
from src.services.people.roles import RoleService
from src.services.people.service import PeopleService
from src.services.people.store import People
from src.services.phone_numbers import PhoneNumberService
from src.services.plans import PlanService
from src.services.platform import PlatformData
from src.services.platform.home import HomeService
from src.services.roles import RoleStore
from src.services.sandbox import SandboxService
from src.services.tokens.keys import KeyStore
from src.services.tokens.replay import ReplayGuard
from src.services.tokens.verify import TokenVerifier
from src.services.tools import ToolService
from src.services.usage import UsageService
from src.services.voice import VoiceService
from src.services.webhook import WebhookService
from src.storage import FileStore, Storage
from src.tunables import tunable
from src.vault import SecretManager, Vault


@dataclass
class Services:
    settings: Settings | None
    db: Database
    payment: ByMode[PaymentGateway]
    keys: KeyStore
    roles: RoleStore
    registry: AppRegistry
    verifier: TokenVerifier
    billing: ByMode[BillingService]
    admin: AdminService
    tools: ToolService
    plans: ByMode[PlanService]
    coupons: ByMode[CouponService]
    webhooks: ByMode[WebhookService]
    businesses: BusinessService
    voice: VoiceService
    sandbox: SandboxService
    usage: UsageService
    numbers: PhoneNumberService
    customers: CustomerService
    handoff: HandoffService
    accounts: AccountProvider
    people: PeopleService
    members: MemberService
    role_service: RoleService
    platform_data: PlatformData
    home: HomeService
    advertisements: AdvertisementService
    actors: ActorResolver
    storage: FileStore
    media: MediaRules
    ivr: IvrService
    callers: CallerService
    agent_tokens: AgentTokenService
    identity: PasswordSignIn | None = None


def console_url(settings: Settings | None) -> str:
    """Where a handoff lands: the local console in development, no extra env needed."""
    local = settings is not None and settings.environment == "development"
    return tunable("handoff.localConsoleUrl" if local else "handoff.consoleUrl")


def assemble(
    settings: Settings | None, db: Database, payment: ByMode[PaymentGateway], accounts: AccountProvider,
    storage: FileStore, vault: Vault, numbers: NumberProvider, remote: RemoteService,
    identity: PasswordSignIn | None = None,
) -> Services:
    """Every dependency injected, so tests pass fakes for the external ones. Stripe-backed services
    come once per mode (`payment` has a test gateway only when test mode is configured)."""
    keys, roles, people = KeyStore(db), RoleStore(db), People(db)
    plans = payment.map(lambda gateway, _: PlanService(db, gateway))
    billing = payment.map(lambda gateway, _: BillingService(gateway))
    registry = AppRegistry(db, plans)
    admin = AdminService(db, keys, payment, plans, forget=registry.forget)
    tools = ToolService(db, keys)
    businesses = BusinessService(db, accounts, billing, identity)
    usage = UsageService(payment, billing, businesses)
    ads = AdvertisementService(db, businesses)
    people_service, platform_data = PeopleService(accounts, people), PlatformData(db, people)
    hub_url, project = (settings.service_base_url, project_id()) if settings else ("", "")
    voice = VoiceService(db, businesses, tools, vault, numbers, remote, hub_url, project)
    webhooks = payment.map(lambda gateway, mode: WebhookService(
        gateway, billing[mode], plans[mode], keys, mode, businesses.checklist.on_subscription))
    return Services(
        settings=settings, db=db, payment=payment, keys=keys, roles=roles,
        registry=registry, verifier=TokenVerifier(keys, ReplayGuard()),
        billing=billing, admin=admin, tools=tools, plans=plans,
        coupons=plans.map(lambda plan_service, mode: CouponService(payment[mode], plan_service)),
        webhooks=webhooks,
        businesses=businesses, voice=voice, sandbox=SandboxService(db, businesses, voice),
        usage=usage, numbers=PhoneNumberService(db, numbers, billing, businesses, voice),
        customers=CustomerService(payment, billing, businesses, usage),
        handoff=HandoffService(db, businesses, accounts, console_url(settings)),
        accounts=accounts, people=people_service, members=MemberService(accounts),
        role_service=RoleService(people, roles), platform_data=platform_data,
        home=HomeService(businesses, people_service, ads, admin, platform_data),
        advertisements=ads,
        actors=ActorResolver(people, businesses), storage=storage,
        media=MediaRules(accounts, people, businesses, ads),
        ivr=IvrService(businesses, storage), callers=CallerService(db),
        agent_tokens=AgentTokenService(db, roles), identity=identity,
    )


def build(settings: Settings) -> Services:
    return assemble(
        settings=settings, db=DB(project=project_id(), location=region()),
        payment=ByMode(
            Payment(settings.stripe_secret_key, settings.stripe_webhook_secret),
            Payment(settings.stripe_test_secret_key, settings.stripe_test_webhook_secret)
            if settings.stripe_test_secret_key else None),
        accounts=Accounts(), storage=Storage(), vault=SecretManager(project_id()),
        numbers=Numbers(settings.twilio_account_sid, settings.twilio_auth_token), remote=Remote(),
        identity=PasswordSignIn(settings.firebase_api_key) if settings.firebase_api_key else None,
    )
