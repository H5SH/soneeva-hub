from fastapi import APIRouter

from app.dependencies import RegistryDep, SettingsDep
from app.models.api import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDep, registry: RegistryDep) -> HealthResponse:
    return HealthResponse(status="ok", environment=settings.environment, apps=await registry.ids())
