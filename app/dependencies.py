from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status

from app.config import AppRegistry, Settings
from app.models.app import AppConfig
from app.models.auth import AuthContext, TokenClaims
from app.services.billing import BillingService
from app.services.webhook import WebhookService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> AppRegistry:
    return request.app.state.registry


def get_billing(request: Request) -> BillingService:
    return request.app.state.billing


def get_webhooks(request: Request) -> WebhookService:
    return request.app.state.webhooks


def get_app(
    app_id: Annotated[str, Path(description="Which BigCommerce app the caller is acting for")],
    registry: Annotated[AppRegistry, Depends(get_registry)],
) -> AppConfig:
    app = registry.get(app_id)
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown app '{app_id}'")
    return app


def get_auth(request: Request, app: Annotated[AppConfig, Depends(get_app)]) -> AuthContext:
    claims: TokenClaims | None = getattr(request.state, "claims", None)
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing x-api-key")
    if claims.app_id and claims.app_id != app.app_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token is not valid for this app")
    return AuthContext(
        app_id=app.app_id,
        store_hash=claims.store_hash,
        access_token=claims.access_token,
        claimed_plan=claims.plan,
        email=claims.email,
    )


SettingsDep = Annotated[Settings, Depends(get_settings)]
RegistryDep = Annotated[AppRegistry, Depends(get_registry)]
AppDep = Annotated[AppConfig, Depends(get_app)]
AuthDep = Annotated[AuthContext, Depends(get_auth)]
BillingDep = Annotated[BillingService, Depends(get_billing)]
WebhookDep = Annotated[WebhookService, Depends(get_webhooks)]
