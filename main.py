import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth.keys import KeyStore
from app.auth.replay import ReplayGuard
from app.auth.verify import TokenVerifier
from app.config import AppRegistry, get_settings
from app.routes import ROUTERS
from app.services.billing import BillingService
from app.services.db import DB
from app.services.payment import Payment
from app.services.webhook import WebhookService

DESCRIPTION = """
Central billing service for the Codinative apps.
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
    # Auth needs the database, so it is built here rather than at create_app() time — that
    # ordering is why the old middleware could never reach a key store.
    keys = KeyStore(db)
    app.state.verifier = TokenVerifier(keys, ReplayGuard())
    app.state.webhooks = WebhookService(db, payment, billing, keys)
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
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)