"""Everything routes depend on, in one place.

Two audiences, kept apart: `/v1` callers are machines holding an Ed25519 key, `/admin` is a
person holding a session. Nothing bridges them.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.responses import RedirectResponse

from app.config import AppRegistry, Settings
from app.models.admin import Principal, Scope
from app.models.app import AppConfig
from app.services.admin import AdminService
from app.services.auth import Auth, AuthError
from app.services.billing import BillingService
from app.services.coupons import CouponService
from app.services.payment import PaymentGateway
from app.services.tokens.deps import ApiAuthDep, HostedAuthDep
from app.services.webhook import WebhookService

# ── wiring ───────────────────────────────────────────────────────────────────


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> AppRegistry:
    return request.app.state.registry


def get_billing(request: Request) -> BillingService:
    return request.app.state.billing


def get_webhooks(request: Request) -> WebhookService:
    return request.app.state.webhooks


def get_admin(request: Request) -> AdminService:
    return request.app.state.admin


def get_coupons(request: Request) -> CouponService:
    return request.app.state.coupons


def get_payment(request: Request) -> PaymentGateway:
    return request.app.state.payment


def get_auth(request: Request) -> Auth:
    return request.app.state.authenticator


async def get_app(
    app_id: Annotated[str, Path(description="Which app the caller is acting for")],
    registry: AppRegistry = Depends(get_registry),
) -> AppConfig:
    app = await registry.get(app_id)
    if not app:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown app '{app_id}'")
    return app


SettingsDep = Annotated[Settings, Depends(get_settings)]
RegistryDep = Annotated[AppRegistry, Depends(get_registry)]
AppDep = Annotated[AppConfig, Depends(get_app)]
BillingDep = Annotated[BillingService, Depends(get_billing)]
WebhookDep = Annotated[WebhookService, Depends(get_webhooks)]
AdminDep = Annotated[AdminService, Depends(get_admin)]
CouponDep = Annotated[CouponService, Depends(get_coupons)]
PaymentDep = Annotated[PaymentGateway, Depends(get_payment)]

# Machine callers. Declared on the router, not per route — every /v1 route needs one.
AuthDep = ApiAuthDep
HostedDep = HostedAuthDep

# ── admin sessions ───────────────────────────────────────────────────────────

SESSION_PATH = "/"
DEFAULT_NEXT = "/admin"


class NeedsLogin(Exception):
    """No usable session. Raised so a browser is redirected rather than shown a 401."""


async def current_user(request: Request) -> Principal:
    name = request.app.state.settings.session_cookie_name
    cookie = request.cookies.get(name)
    if not cookie:
        raise NeedsLogin
    try:
        return await get_auth(request).principal(cookie)
    except AuthError as error:
        raise NeedsLogin from error


PrincipalDep = Annotated[Principal, Depends(current_user)]


def requires(scope: Scope):
    """Scope check for routes with no app in the path."""

    async def check(user: PrincipalDep) -> Principal:
        if not user.may(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {scope}")
        return user

    return Depends(check)


def requires_app(scope: Scope):
    """Scope *and* membership, for routes under /admin/apps/{app_id}.

    Both halves matter: the scope says the action is allowed at all, membership says it is
    allowed here. A serviceperson with Key.Rotate who is not on an app must not rotate it.
    """

    async def check(user: PrincipalDep, app_id: Annotated[str, Path()]) -> Principal:
        if not user.may(scope, app_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {scope} on {app_id}")
        return user

    return Depends(check)


def is_https(request: Request) -> bool:
    """Whether the browser reached us over TLS.

    From the request, not a config flag: a Secure cookie is silently dropped over plain http,
    so an environment name gets this wrong on localhost. X-Forwarded-Proto covers Cloud Run,
    where the app only ever sees http.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


def safe_next(candidate: str | None) -> str:
    """A path we will send a browser to after sign-in.

    Unchecked, `?next=https://evil.com` hands the user over right after they type a password.
    `//host` is protocol-relative and leaves the site despite the leading slash.
    """
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return DEFAULT_NEXT
    return candidate if candidate.startswith("/admin") else DEFAULT_NEXT


def login_redirect(request: Request) -> RedirectResponse:
    """Only GETs are remembered — redirecting to a POST-only path after sign-in is a 405."""
    target = request.url.path
    remember = request.method == "GET" and target and target != "/admin/login"
    suffix = f"?next={target}" if remember else ""
    return RedirectResponse(f"/admin/login{suffix}", status_code=status.HTTP_303_SEE_OTHER)
