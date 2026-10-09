import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from app.config import AppRegistry, get_settings
from app.dependencies import NeedsLogin, login_redirect
from app.models.admin import DEFAULT_ROLES
from app.models.db import Collection
from app.routes import ROUTERS
from app.services.admin import AdminService
from app.services.auth import Auth
from app.services.billing import BillingService
from app.services.coupons import CouponService
from app.services.db import DB
from app.services.payment import Payment
from app.services.roles import RoleStore
from app.services.tokens.keys import KeyStore
from app.services.tokens.replay import ReplayGuard
from app.services.tokens.verify import TokenVerifier
from app.services.webhook import WebhookService

DESCRIPTION = """
Central billing service for the Codinative apps.
"""


async def seed_roles(db: DB) -> None:
    """Write the default roles once. They are data, so the dashboard can edit them after."""
    for role, scopes in DEFAULT_ROLES.items():
        if not await db.get_row(Collection.ROLES, role):
            await db.set_row(Collection.ROLES, role, {"scopes": [str(s) for s in scopes]})


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    payment = Payment(settings.stripe_secret_key, settings.stripe_webhook_secret)
    db = DB()
    billing = BillingService(db, payment)

    app.state.settings = settings
    app.state.registry = AppRegistry(db)
    app.state.billing = billing
    # Auth needs the database, so it is built here rather than at create_app() time — that
    # ordering is why the old middleware could never reach a key store.
    keys = KeyStore(db)
    roles = RoleStore(db)
    app.state.verifier = TokenVerifier(keys, ReplayGuard())
    app.state.webhooks = WebhookService(db, payment, billing, keys)
    app.state.authenticator = Auth(db, settings.session_secret, settings.session_ttl_days, roles)
    app.state.payment = payment
    app.state.admin = AdminService(db, keys, roles)
    app.state.coupons = CouponService(db, payment)
    await seed_roles(db)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Payment Service",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    for router in ROUTERS:
        app.include_router(router)

    @app.exception_handler(NeedsLogin)
    async def _needs_login(request: Request, _: NeedsLogin) -> RedirectResponse:
        """A signed-out browser gets the login page, not a 401 it cannot act on."""
        return login_redirect(request)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
