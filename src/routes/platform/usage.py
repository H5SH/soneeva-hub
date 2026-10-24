"""soneeva-voice's calls, each with its business's own key (sub = businessId)."""

from fastapi import APIRouter

from src.dependencies import BillingsDep, BusinessDep, UsageDep
from src.models.admin import Scope
from src.models.platform import Caller, UsageReport
from src.services.errors import PlatformError
from src.services.tokens.caller import requires_caller

router = APIRouter(prefix="/v1", tags=["platform"])


@router.post("/usage")
async def report_usage(
    body: UsageReport, usage: UsageDep, caller: Caller = requires_caller(Scope.HUB_USAGE_WRITE)
) -> dict:
    """After each call. Safe to retry."""
    if not caller.business_id:
        raise PlatformError("Usage is reported with sub = businessId", 403)
    return await usage.record(caller.business_id, body)


@router.get("/businesses/{business_id}/plan")
async def plan_status(
    business_id: str, businesses: BusinessDep, billings: BillingsDep,
    caller: Caller = requires_caller(Scope.HUB_PLAN_VIEW),
) -> dict:
    """Whether the business's subscription grants service, from Stripe (briefly cached)."""
    if caller.business_id != business_id:
        raise PlatformError("Call with sub = businessId", 403)
    business = await businesses.require(caller, business_id)
    if business.sandboxOf:  # a developer's local voice: always served, never billed
        return {"active": True, "status": "sandbox", "plan": None, "periodEnd": None, "test": True}
    record = await billings[business.mode].get_record(business.appId, business.id)
    end = record.current_period_end
    return {"active": record.is_active, "status": record.status, "plan": record.plan_code,
            "periodEnd": end.isoformat() if end else None, "test": business.test}
