from fastapi import APIRouter

from src.dependencies import AdminDep, AppRefDep, SettingsDep
from src.models.admin import Principal, Scope
from src.models.mode import Mode
from src.models.staff import AppCreate, AppPatch, app_view
from src.services.admin import AdminService
from src.services.errors import PlatformError
from src.services.tokens.staff import need, requires_staff

router = APIRouter(prefix="/v1/payment/apps", tags=["payment"])
VIEW, UPDATE = Scope.HUB_PAYMENT_VIEW, Scope.HUB_PAYMENT_UPDATE


async def shown(admin: AdminService, app_id: str, mode: Mode = Mode.LIVE) -> dict:
    """The app with its plans in `mode` and its subscribers (live and test). A `_staging` id is no
    app of its own: 404 when passed raw."""
    config = await admin.get_app(app_id, mode)
    if not config:
        raise PlatformError("No such app", 404)
    view = app_view(config, await admin.get_application(app_id), mode)
    return view | {"subscribers": await admin.subscribers(app_id)}


@router.get("")
async def list_apps(
    admin: AdminDep, q: str = "", person: Principal = requires_staff(VIEW, Scope.APP_VIEW)
) -> dict:
    """`q` matches the id, the name or any tag."""
    return {"apps": [app_view(config, creds) for config, creds in await admin.list_apps(person, q)]}


@router.post("", status_code=201)
async def create_app(
    body: AppCreate, admin: AdminDep, person: Principal = requires_staff(UPDATE, Scope.APP_CREATE)
) -> dict:
    hosts = [h.strip() for h in body.allowedReturnHosts if h.strip()]
    config = await admin.create_app(body.id, body.name.strip(), body.returnUrl.strip(), hosts, body.tags)
    return await shown(admin, config.id)


@router.get("/{app_id}")
async def get_app(ref: AppRefDep, admin: AdminDep, person: Principal = requires_staff(VIEW)) -> dict:
    """`{appId}_staging`: the same app with its Stripe test plans."""
    need(person, Scope.APP_VIEW, ref.id)
    return await shown(admin, ref.id, ref.mode)


@router.patch("/{app_id}")
async def update_app(
    app_id: str, body: AppPatch, admin: AdminDep, person: Principal = requires_staff(UPDATE)
) -> dict:
    need(person, Scope.APP_UPDATE, app_id)
    await shown(admin, app_id)
    if body.fields():
        await admin.update_app(app_id, **body.fields())
    return await shown(admin, app_id)


@router.post("/{app_id}/webhook", status_code=201)
async def provision_webhook(
    ref: AppRefDep, admin: AdminDep, settings: SettingsDep, person: Principal = requires_staff(UPDATE)
) -> dict:
    """Creates the app's Stripe endpoint (`_staging`: in the test account) and stores its signing
    secret, which is never returned."""
    need(person, Scope.APP_UPDATE, ref.id)
    await shown(admin, ref.id)
    base_url = settings.service_base_url if settings else ""
    endpoint_id, url = await admin.provision_webhook(ref.id, base_url, ref.mode)
    return {"endpointId": endpoint_id, "url": url, "hasWebhookSecret": True}


@router.delete("/{app_id}")
async def delete_app(app_id: str, admin: AdminDep, person: Principal = requires_staff(UPDATE)) -> dict:
    """Refused (400) while the app has subscribers, live or test."""
    need(person, Scope.APP_DELETE, app_id)
    await shown(admin, app_id)
    await admin.delete_app(app_id)
    return {"deleted": True}


@router.post("/{app_id}/keys", status_code=201)
async def rotate_key(app_id: str, admin: AdminDep, person: Principal = requires_staff(UPDATE)) -> dict:
    """A new key; the others retire. The private key is returned once and never stored."""
    need(person, Scope.KEY_ROTATE, app_id)
    await shown(admin, app_id)
    kid, private_pem = await admin.rotate_key(app_id)
    return {"appId": app_id, "kid": kid, "privateKey": private_pem}
