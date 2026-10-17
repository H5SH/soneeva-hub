"""/v1/agent-tokens: karbon-admin mints and revokes operator tokens for the signed-in person."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.models.admin import Principal, Scope
from src.services.tokens.staff import requires_staff

router = APIRouter(prefix="/v1/agent-tokens", tags=["agent"])


class AgentTokenCreate(BaseModel):
    minutes: int | None = Field(default=None, description="15–120 (settings.json agent.*); default 30.")


@router.post("", status_code=201)
async def mint(body: AgentTokenCreate, request: Request,
               person: Principal = requires_staff(Scope.HUB_PAYMENT_UPDATE)) -> dict:
    """Sub `-`, uid = the person, whose role holds Agent.Use. `{token, expiresAt}` once; only a hash
    is kept."""
    return await request.app.state.agent_tokens.mint(person, body.minutes)


@router.delete("")
async def revoke(request: Request, person: Principal = requires_staff(Scope.HUB_PAYMENT_UPDATE)) -> dict:
    """Every token this person minted stops working at once."""
    return {"revoked": await request.app.state.agent_tokens.revoke_all(person.uid)}
