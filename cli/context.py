"""Service wiring for the command line.

The same objects `main.py` builds, so a command and the equivalent dashboard click run the
same code. Anything with its own write logic belongs in a service, not here.

**No scope checks.** Running this needs GOOGLE_APPLICATION_CREDENTIALS, which already grants
full Firestore access — a scope check at this layer would be theatre. Scopes exist to
constrain people holding a dashboard session, which is a different thing.
"""

from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

from app.config import get_settings  # noqa: E402
from app.models.admin import SUPERADMIN, Principal, Scope  # noqa: E402
from app.services.admin import AdminService  # noqa: E402
from app.services.auth import Auth  # noqa: E402
from app.services.billing import BillingService  # noqa: E402
from app.services.coupons import CouponService  # noqa: E402
from app.services.db import DB  # noqa: E402
from app.services.payment import Payment  # noqa: E402
from app.services.roles import RoleStore  # noqa: E402
from app.services.tokens.keys import KeyStore  # noqa: E402

OPERATOR = Principal(
    uid="cli", email="cli@localhost", role=SUPERADMIN, scopes=list(Scope), apps=[]
)


@dataclass
class Context:
    admin: AdminService
    coupons: CouponService
    billing: BillingService
    auth: Auth
    settings: object

    @classmethod
    def build(cls) -> "Context":
        settings = get_settings()
        db = DB()
        payment = Payment(settings.stripe_secret_key, settings.stripe_webhook_secret)
        roles = RoleStore(db)
        return cls(
            admin=AdminService(db, KeyStore(db), roles, payment),
            coupons=CouponService(db, payment),
            billing=BillingService(db, payment),
            auth=Auth(db, settings.session_secret, settings.session_ttl_days, roles),
            settings=settings,
        )
