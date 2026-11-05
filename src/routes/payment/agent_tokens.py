"""/v1/agent-tokens: karbon-admin mints and revokes operator tokens for the signed-in person."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.dependencies import AgentTokensDep
from src.models.admin import Scope
from src.models.identity import Identity
from src.services.tokens.staff import requires_staff

router = APIRouter(prefix="/v1/agent-tokens", tags=["agent"])


class AgentTokenCreate(BaseModel):
    minutes: int | None = Field(default=None, description="15–120 (settings.json agent.*); default 30.")


@router.post("", status_code=201)
async def mint(body: AgentTokenCreate, tokens: AgentTokensDep,
               person: Identity = requires_staff(Scope.HUB_PAYMENT_UPDATE)) -> dict:
    """Sub `-`, uid = the person, whose role holds Agent.Use. `{token, expiresAt}` once; only a hash
    is kept."""
    return await tokens.mint(person, body.minutes)


@router.delete("")
async def revoke(
    tokens: AgentTokensDep, person: Identity = requires_staff(Scope.HUB_PAYMENT_UPDATE),
) -> dict:
    """Every token this person minted stops working at once."""
    return {"revoked": await tokens.revoke_all(person.uid)}
