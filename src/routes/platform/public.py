from fastapi import APIRouter, Query

from src.dependencies import PlansDep
from src.models.mode import staged

router = APIRouter(tags=["public"])


@router.get("/v1/public/plans")
async def public_plans(plans: PlansDep, app: str = Query(min_length=1)) -> dict:
    """Active plans with price (major units) and unit. No auth: the site reads it. `{app}_staging`: test."""
    app_id, mode = staged(app)
    return {"plans": await plans[mode].public(app_id)}
