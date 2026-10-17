from fastapi import APIRouter

from src.models.api import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Public liveness probe. Names nothing configured here: the app roster was free reconnaissance."""
    return HealthResponse(status="ok")
