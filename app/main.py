import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import AppRegistry, get_settings
from app.middleware.auth import AuthMiddleware
from app.routes import ROUTERS
from app.services.billing import BillingService
from app.services.db import DB
from app.services.payment import Payment
from app.services.webhook import WebhookService

DESCRIPTION = """
Central billing service for the BigCommerce apps.

Authenticate every call with `x-api-key`: a JWT signed with the shared secret, carrying
`store_hash`, `access_token`, `plan` and an `exp`. Browser redirects into `/billing/...`
accept the same token as `?token=` instead, since a redirect cannot set headers.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    payment = Payment(settings.stripe_secret_key, settings.stripe_webhook_secret)
    db = DB()
    billing = BillingService(db, payment)

    app.state.settings = settings
    app.state.registry = AppRegistry()
    app.state.billing = billing
    app.state.webhooks = WebhookService(db, payment, billing)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Payment Service",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_middleware(AuthMiddleware, secret=settings.api_secret)
    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
