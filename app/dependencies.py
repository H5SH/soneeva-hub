from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status

from app.auth.deps import ApiAuthDep, HostedAuthDep
from app.config import AppRegistry, Settings
from app.models.app import AppConfig
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
    registry: AppRegistry = Depends(get_registry),
) -> AppConfig:
    app = registry.get(app_id)
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown app '{app_id}'")
    return app


SettingsDep = Annotated[Settings, Depends(get_settings)]
RegistryDep = Annotated[AppRegistry, Depends(get_registry)]
AppDep = Annotated[AppConfig, Depends(get_app)]
BillingDep = Annotated[BillingService, Depends(get_billing)]
WebhookDep = Annotated[WebhookService, Depends(get_webhooks)]

# Auth is declared per route rather than applied globally: a route is public precisely when it
# asks for neither of these, instead of being matched against a list of path shapes.
AuthDep = ApiAuthDep
HostedDep = HostedAuthDep