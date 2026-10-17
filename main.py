import logging
import os
from contextlib import asynccontextmanager
from dataclasses import fields

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.routes import ROUTERS
from src.services.catalogue import seed_hub
from src.services.errors import AdminError, PlatformError
from src.wiring import Services, build

DESCRIPTION = "karbon-hub: auth, scopes, plans, payments, businesses and numbers for Soneeva and its apps."


def attach(app: FastAPI, services: Services) -> None:
    """Every service on app.state, under the name the dependencies read."""
    for field in fields(services):
        setattr(app.state, field.name, getattr(services, field.name))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    services = build(settings)
    attach(app, services)
    await seed_hub(services.db)
    yield


def create_app() -> FastAPI:
    """API docs only in development: nothing browser-facing is served in production."""
    docs = os.getenv("ENVIRONMENT", "production") == "development"
    app = FastAPI(
        title="karbon-hub",
        description=DESCRIPTION,
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    for router in ROUTERS:
        app.include_router(router)

    @app.exception_handler(AdminError)
    async def _admin_error(_: Request, error: AdminError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(PlatformError)
    async def _platform_error(_: Request, error: PlatformError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    return app


app = create_app()
