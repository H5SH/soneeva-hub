"""/v1/agent: the setup an operator's agent may do over HTTP, with `Authorization: Bearer kat_…`.

Apps, plans, the Stripe webhook, keys, coupons and entitlements; never people or roles. Each route
re-checks the same payment.* scope (and app membership) as its /v1/payment twin and calls the same
service method, so the API, karbon-admin and the CLI cannot drift. Where the twin takes
`{appId}_staging` (Stripe test mode), so does the route here.
"""

from fastapi import APIRouter, Query
from fastapi.openapi.utils import get_openapi
from fastapi.responses import PlainTextResponse

from src.dependencies import AdminDep, AppRefDep, BillingDep, CouponDep, PlanDep, SettingsDep
from src.models.admin import Scope
from src.models.staff import AppCreate, AppPatch, CouponCreate, PlanCreate, app_view, coupon_view
from src.routes.payment.apps import shown
from src.routes.payment.plans import save_as
from src.services.errors import AdminError
from src.services.tokens.agent import Operator, OperatorDep, agent_requires
from src.services.tokens.generate import env_block
from src.services.tokens.staff import need
from src.services.webhook import WEBHOOK_EVENTS
from src.utils import slug

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class AgentAppCreate(AppCreate):
    freePlanCode: str | None = None


@router.get("/whoami")
async def whoami(person: OperatorDep) -> dict:
    """Call first: fails fast on a bad or expired token and says what it may do, and until when."""
    return {"uid": person.uid, "email": person.email, "role": person.role or None,
            "paymentScopes": [str(s) for s in person.scopes], "allApps": person.sees_all,
            "apps": person.apps, "expiresAt": person.expires_at.isoformat()}


@router.get("/apps")
async def list_apps(admin: AdminDep, q: str = "", person: Operator = agent_requires(Scope.APP_VIEW)) -> dict:
    return {"apps": [app_view(config, creds) for config, creds in await admin.list_apps(person, q)]}


@router.get("/apps/{app_id}")
async def get_app(ref: AppRefDep, admin: AdminDep, person: Operator = agent_requires(Scope.APP_VIEW)) -> dict:
    need(person, Scope.APP_VIEW, ref.id)
    return await shown(admin, ref.id, ref.mode)


@router.post("/apps", status_code=201)
async def create_app(
    body: AgentAppCreate, admin: AdminDep, person: Operator = agent_requires(Scope.APP_CREATE)
) -> dict:
    hosts = [h.strip() for h in body.allowedReturnHosts if h.strip()]
    config = await admin.create_app(body.id, body.name.strip(), body.returnUrl.strip(), hosts, body.tags)
    if body.freePlanCode:
        await admin.update_app(config.id, free_plan_code=body.freePlanCode)
    return await shown(admin, config.id)


@router.patch("/apps/{app_id}")
async def update_app(
    app_id: str, body: AppPatch, admin: AdminDep, person: Operator = agent_requires(Scope.APP_UPDATE)
) -> dict:
    """Partial; `webhookSecret` is write-only (the response shows `hasWebhookSecret`)."""
    need(person, Scope.APP_UPDATE, app_id)
    await shown(admin, app_id)
    if not body.fields():
        raise AdminError("No fields to change.")
    await admin.update_app(app_id, **body.fields())
    return await shown(admin, app_id)


@router.post("/apps/{app_id}/plans")
async def save_plan(
    ref: AppRefDep, body: PlanCreate, admin: AdminDep, plans: PlanDep, person: OperatorDep
) -> dict:
    """Create or update by code (derived from the name when blank). `amount` is minor units; a
    different amount makes a new Stripe price (subscribers keep the old one)."""
    await shown(admin, ref.id, ref.mode)
    code = body.code or slug(body.name)
    if not code:
        raise AdminError("Give the plan a name.")
    return await save_as(plans, person, ref.id, code, body)


@router.post("/apps/{app_id}/webhook", status_code=201)
async def provision_webhook(
    ref: AppRefDep, admin: AdminDep, settings: SettingsDep,
    person: Operator = agent_requires(Scope.APP_UPDATE),
) -> dict:
    """Creates the app's Stripe endpoint and stores its signing secret, which is never returned."""
    need(person, Scope.APP_UPDATE, ref.id)
    await shown(admin, ref.id)
    base_url = settings.service_base_url if settings else ""
    endpoint_id, url = await admin.provision_webhook(ref.id, base_url, ref.mode)
    return {"endpointId": endpoint_id, "url": url, "hasWebhookSecret": True, "events": list(WEBHOOK_EVENTS)}


@router.post("/apps/{app_id}/keys", status_code=201, response_model=None)
async def rotate_key(
    app_id: str, admin: AdminDep, fmt: str = Query("json", alias="format", pattern="^(json|env)$"),
    person: Operator = agent_requires(Scope.KEY_ROTATE),
) -> dict | PlainTextResponse:
    """The private key is in this response and nowhere else. `?format=env` answers the
    PAYMENT_APPLICATION_ID / PAYMENT_KEY_ID / PAYMENT_PRIVATE_KEY lines as text/plain, to redirect
    straight into an env file without the key passing through a transcript."""
    need(person, Scope.KEY_ROTATE, app_id)
    await shown(admin, app_id)
    kid, private_pem = await admin.rotate_key(app_id)
    if fmt == "env":
        return PlainTextResponse(env_block(app_id, kid, private_pem) + "\n", status_code=201)
    return {"appId": app_id, "kid": kid, "privateKey": private_pem}


@router.post("/apps/{app_id}/coupons", status_code=201)
async def create_coupon(
    ref: AppRefDep, body: CouponCreate, admin: AdminDep, coupons: CouponDep,
    person: Operator = agent_requires(Scope.COUPON_CREATE),
) -> dict:
    need(person, Scope.COUPON_CREATE, ref.id)
    await shown(admin, ref.id, ref.mode)
    try:
        terms = body.discount()
    except ValueError as error:
        raise AdminError(str(error)) from error
    return coupon_view(await coupons.create(ref.id, body.code, person.email or person.uid, terms))


@router.get("/apps/{app_id}/tenants/{tenant}")
async def entitlements(
    ref: AppRefDep, tenant: str, admin: AdminDep, billing: BillingDep,
    person: Operator = agent_requires(Scope.APP_VIEW),
) -> dict:
    """What the consuming app sees at GET /v1/apps/{id}/entitlements for this tenant."""
    need(person, Scope.APP_VIEW, ref.id)
    config = await admin.get_app(ref.id, ref.mode)
    if not config:
        raise AdminError(f"No app '{ref.id}'.")
    return billing.to_entitlements(config, await billing.get_record(ref.id, tenant)).model_dump(mode="json")


schema = APIRouter(tags=["agent"])


@schema.get("/v1/agent/openapi.json", include_in_schema=False)
async def agent_openapi() -> dict:
    """Public: the agent API's own schema, so an HTTP-only agent can read the real shapes."""
    return get_openapi(title="karbon-hub agent API", version="1", routes=router.routes,
                       description=__doc__ or "")
