"""An app's plans and coupons. `{app_id}` may be `{appId}_staging`: Stripe test mode, its own plans."""

from fastapi import APIRouter, Request

from src.dependencies import AdminDep, AppRefDep, CouponDep, PlanDep
from src.models.admin import Principal, Scope
from src.models.staff import CouponCreate, PlanCreate, PlanPut, PlanStatusPut, coupon_view, plan_view
from src.routes.payment.apps import shown
from src.routes.platform.media import read_file
from src.services.errors import AdminError, PlatformError
from src.services.plans import PlanService
from src.services.tokens.staff import need, requires_staff
from src.utils import slug

router = APIRouter(prefix="/v1/payment/apps/{app_id}", tags=["payment"])
VIEW, UPDATE = Scope.HUB_PAYMENT_VIEW, Scope.HUB_PAYMENT_UPDATE


@router.get("/plans")
async def list_plans(ref: AppRefDep, admin: AdminDep, person: Principal = requires_staff(VIEW)) -> dict:
    """`{appId}_staging`: the plans in Stripe test mode."""
    need(person, Scope.PLAN_VIEW, ref.id)
    return {"plans": (await shown(admin, ref.id, ref.mode))["plans"]}


async def save_as(plans: PlanService, person: Principal, app_id: str, code: str, body: PlanPut) -> dict:
    """payment.plan.update when the code exists, payment.plan.create when it does not."""
    need(person, Scope.PLAN_UPDATE if await plans.get(app_id, code) else Scope.PLAN_CREATE, app_id)
    plan = await plans.define(
        app_id, code, body.name, body.amount, body.currency, body.interval, body.trialDays,
        [f.strip() for f in body.features if f.strip()], unit=body.unit, status=body.status,
        contact=body.contact, description=body.description,
    )
    return plan_view(plan)


@router.post("/plans", status_code=201)
async def create_plan(
    ref: AppRefDep, body: PlanCreate, admin: AdminDep, plans: PlanDep,
    person: Principal = requires_staff(UPDATE),
) -> dict:
    """The code is derived from the name unless given; an existing code is refused (409)."""
    await shown(admin, ref.id, ref.mode)
    code = body.code or slug(body.name)
    if not code:
        raise AdminError("Give the plan a name.")
    if await plans.get(ref.id, code):
        raise PlatformError(f"A plan '{code}' already exists", 409)
    return await save_as(plans, person, ref.id, code, body)


@router.put("/plans/{code}")
async def save_plan(
    ref: AppRefDep, code: str, body: PlanPut, admin: AdminDep, plans: PlanDep,
    person: Principal = requires_staff(UPDATE),
) -> dict:
    """Creates or updates the Stripe product. A different amount, unit or interval makes a new default
    price (metered against the plan's meter) and archives the old one; subscribers keep it."""
    await shown(admin, ref.id, ref.mode)
    return await save_as(plans, person, ref.id, code, body)


@router.put("/plans/{code}/status")
async def set_plan_status(
    ref: AppRefDep, code: str, body: PlanStatusPut, plans: PlanDep, person: Principal = requires_staff(UPDATE)
) -> dict:
    """active is sold, soon is listed without a buy button, hidden is neither."""
    need(person, Scope.PLAN_UPDATE, ref.id)
    if not await plans.get(ref.id, code):
        raise PlatformError("No such plan", 404)
    return plan_view(await plans.set_status(ref.id, code, body.status))


@router.put("/plans/{code}/icon")
async def set_plan_icon(
    ref: AppRefDep, code: str, request: Request, plans: PlanDep, person: Principal = requires_staff(UPDATE)
) -> dict:
    """Raw png, jpeg or webp body (`media.*`), uploaded to Stripe as the product's image."""
    need(person, Scope.PLAN_UPDATE, ref.id)
    if not await plans.get(ref.id, code):
        raise PlatformError("No such plan", 404)
    data, kind = await read_file(request)
    return plan_view(await plans.set_icon(ref.id, code, data, kind))


@router.delete("/plans/{code}/icon")
async def delete_plan_icon(
    ref: AppRefDep, code: str, plans: PlanDep, person: Principal = requires_staff(UPDATE)
) -> dict:
    need(person, Scope.PLAN_UPDATE, ref.id)
    if not await plans.get(ref.id, code):
        raise PlatformError("No such plan", 404)
    return plan_view(await plans.set_icon(ref.id, code, None))


@router.delete("/plans/{code}")
async def delete_plan(
    ref: AppRefDep, code: str, plans: PlanDep, person: Principal = requires_staff(UPDATE)
) -> dict:
    """Archives its Stripe product and price; existing subscribers keep billing."""
    need(person, Scope.PLAN_DELETE, ref.id)
    if not await plans.get(ref.id, code):
        raise PlatformError("No such plan", 404)
    await plans.delete(ref.id, code)
    return {"deleted": True}


@router.get("/coupons")
async def list_coupons(
    ref: AppRefDep, coupons: CouponDep, person: Principal = requires_staff(VIEW)
) -> dict:
    need(person, Scope.COUPON_VIEW, ref.id)
    return {"coupons": [coupon_view(c) for c in await coupons.list_for_app(ref.id)]}


@router.post("/coupons", status_code=201)
async def create_coupon(
    ref: AppRefDep, body: CouponCreate, admin: AdminDep, coupons: CouponDep,
    person: Principal = requires_staff(UPDATE),
) -> dict:
    """A Stripe promotion code with the given terms (default 100% off forever); generated when
    `code` is blank. Incoherent terms are refused (400) before anything reaches Stripe."""
    need(person, Scope.COUPON_CREATE, ref.id)
    await shown(admin, ref.id, ref.mode)
    try:
        terms = body.discount()
    except ValueError as error:
        raise AdminError(str(error)) from error
    coupon = await coupons.create(ref.id, body.code, person.email or person.uid, terms)
    return coupon_view(coupon)


@router.delete("/coupons/{code}")
async def delete_coupon(
    ref: AppRefDep, code: str, coupons: CouponDep, person: Principal = requires_staff(UPDATE)
) -> dict:
    need(person, Scope.COUPON_DELETE, ref.id)
    await coupons.deactivate(ref.id, code)
    return {"deleted": True}
