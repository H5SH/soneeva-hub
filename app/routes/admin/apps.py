from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import AdminDep, CouponDep, PaymentDep, requires, requires_app
from app.models.admin import Principal, Scope
from app.models.app import PlanConfig
from app.services.admin import AdminError
from app.services.tokens.generate import env_block
from app.templating import templates

router = APIRouter(prefix="/admin/apps", tags=["admin"])
SEE_OTHER = status.HTTP_303_SEE_OTHER


def _back(app_id: str, message: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import urlencode

    query = urlencode({k: v for k, v in {"ok": message, "err": error}.items() if v})
    return RedirectResponse(f"/admin/apps/{app_id}?{query}", status_code=SEE_OTHER)


@router.get("", response_class=HTMLResponse)
async def index(request: Request, admin: AdminDep, user: Principal = requires(Scope.APP_VIEW)):
    apps, keys = await admin.list_apps_with_keys(user)
    return templates.TemplateResponse(
        request,
        "admin/apps.html",
        {"user": user, "nav": "apps", "apps": apps, "key_counts": keys, "Scope": Scope},
    )


@router.get("/new", response_class=HTMLResponse)
async def new_app(request: Request, user: Principal = requires(Scope.APP_CREATE)):
    return templates.TemplateResponse(request, "admin/app_new.html", {"user": user, "nav": "apps"})


@router.post("")
async def create_app(
    request: Request,
    admin: AdminDep,
    app_id: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
    return_url: Annotated[str, Form()] = "",
    hosts: Annotated[str, Form()] = "",
    user: Principal = requires(Scope.APP_CREATE),
):
    try:
        config = await admin.create_app(
            app_id.strip().lower(),
            name.strip(),
            return_url.strip(),
            [h.strip() for h in hosts.split(",") if h.strip()],
        )
    except AdminError as error:
        return templates.TemplateResponse(
            request, "admin/app_new.html", {"user": user, "error": str(error)}, status_code=400
        )
    return _back(config.id, "App created. Issue a key to finish setup.")


@router.get("/{app_id}", response_class=HTMLResponse)
async def detail(
    request: Request,
    app_id: str,
    admin: AdminDep,
    coupons: CouponDep,
    ok: str = "",
    err: str = "",
    user: Principal = requires_app(Scope.APP_VIEW),
):
    config = await admin.get_app(app_id)
    if not config:
        return RedirectResponse("/admin", status_code=SEE_OTHER)
    application = await admin.get_application(app_id)
    return templates.TemplateResponse(
        request,
        "admin/app_detail.html",
        {
            "user": user,
            "nav": "apps",
            "app": config,
            "keys": application.keys if application else [],
            "coupons": await coupons.list_for_app(app_id) if user.may(Scope.COUPON_VIEW, app_id) else [],
            "ok": ok,
            "err": err,
            "Scope": Scope,
        },
    )


@router.post("/{app_id}")
async def update_app(
    app_id: str,
    admin: AdminDep,
    name: Annotated[str, Form()] = "",
    return_url: Annotated[str, Form()] = "",
    hosts: Annotated[str, Form()] = "",
    free_plan_code: Annotated[str, Form()] = "",
    user: Principal = requires_app(Scope.APP_UPDATE),
):
    try:
        await admin.update_app(
            app_id,
            name=name.strip(),
            return_url=return_url.strip() or None,
            allowed_return_hosts=[h.strip() for h in hosts.split(",") if h.strip()],
            free_plan_code=free_plan_code.strip() or None,
        )
    except AdminError as error:
        return _back(app_id, error=str(error))
    return _back(app_id, "Saved.")


@router.post("/{app_id}/delete")
async def delete_app(
    app_id: str, admin: AdminDep, user: Principal = requires_app(Scope.APP_DELETE)
):
    try:
        await admin.delete_app(app_id)
    except AdminError as error:
        return _back(app_id, error=str(error))
    return RedirectResponse("/admin", status_code=SEE_OTHER)


@router.post("/{app_id}/keys/rotate", response_class=HTMLResponse)
async def rotate_key(
    request: Request,
    app_id: str,
    admin: AdminDep,
    user: Principal = requires_app(Scope.KEY_ROTATE),
):
    """Shows the private key once. It is never stored and cannot be retrieved again."""
    try:
        kid, private_pem = await admin.rotate_key(app_id)
    except AdminError as error:
        return _back(app_id, error=str(error))
    return templates.TemplateResponse(
        request,
        "admin/app_key.html",
        {"user": user, "nav": "apps", "app_id": app_id, "kid": kid,
         "env": env_block(app_id, kid, private_pem)},
    )


@router.post("/{app_id}/plans")
async def save_plan(
    app_id: str,
    admin: AdminDep,
    payment: PaymentDep,
    code: Annotated[str, Form()],
    name: Annotated[str, Form()],
    amount: Annotated[int, Form()] = 0,
    currency: Annotated[str, Form()] = "usd",
    interval: Annotated[str, Form()] = "month",
    trial_days: Annotated[int, Form()] = 0,
    features: Annotated[str, Form()] = "",
    user: Principal = requires_app(Scope.PLAN_CREATE),
):
    """A paid plan gets a Stripe product and price; a zero amount is the free tier."""
    config = await admin.get_app(app_id)
    if not config:
        return _back(app_id, error="That app no longer exists.")

    existing = config.plan(code.strip())
    product_id = existing.product_id if existing else None
    price_id = existing.price_id if existing else None

    if amount > 0 and not price_id:
        writable, why = await payment.can_write_products()
        if not writable:
            return _back(app_id, error=f"Stripe refused: {why}")
        product_id, price_id = await payment.create_price(
            app_id, code.strip(), name.strip() or code.strip(), amount, currency, interval
        )

    plan = PlanConfig(
        code=code.strip(),
        name=name.strip() or code.strip(),
        price_id=price_id,
        product_id=product_id,
        trial_days=trial_days or None,
        features=[f.strip() for f in features.splitlines() if f.strip()],
    )
    try:
        await admin.save_plan(app_id, plan)
    except AdminError as error:
        return _back(app_id, error=str(error))
    return _back(app_id, f"Plan '{plan.code}' saved.")


@router.post("/{app_id}/plans/{code}/delete")
async def delete_plan(
    app_id: str, code: str, admin: AdminDep, user: Principal = requires_app(Scope.PLAN_DELETE)
):
    """Removes the plan here. The Stripe price stays, so existing subscribers keep billing."""
    try:
        await admin.delete_plan(app_id, code)
    except AdminError as error:
        return _back(app_id, error=str(error))
    return _back(app_id, f"Plan '{code}' removed.")


@router.post("/{app_id}/coupons")
async def create_coupon(
    app_id: str,
    admin: AdminDep,
    coupons: CouponDep,
    code: Annotated[str, Form()] = "",
    max_redemptions: Annotated[int, Form()] = 0,
    user: Principal = requires_app(Scope.COUPON_CREATE),
):
    config = await admin.get_app(app_id)
    products = [p.product_id for p in (config.plans if config else []) if p.product_id]
    try:
        coupon = await coupons.create(
            app_id, code, products, user.email, max_redemptions or None
        )
    except AdminError as error:
        return _back(app_id, error=str(error))
    return _back(app_id, f"Coupon {coupon.code} created — 100% off, forever.")


@router.post("/{app_id}/coupons/{code}/delete")
async def delete_coupon(
    app_id: str, code: str, coupons: CouponDep, user: Principal = requires_app(Scope.COUPON_DELETE)
):
    try:
        await coupons.deactivate(app_id, code)
    except AdminError as error:
        return _back(app_id, error=str(error))
    return _back(app_id, f"Coupon {code} deactivated.")
