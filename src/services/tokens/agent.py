"""`Authorization: Bearer kat_…` for /v1/agent: the person behind an operator token, re-read per call."""

from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from src.models.admin import Principal, Scope
from src.services.tokens.staff import find_principal


class Operator(Principal):
    expires_at: datetime


async def operator(request: Request) -> Operator:
    """401 for a missing, unknown, expired or revoked token, a disabled person, or one whose role no
    longer holds Agent.Use."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    state = request.app.state
    found = await state.agent_tokens.resolve(token.strip()) if scheme.lower() == "bearer" else None
    person = await find_principal(state, found[0]) if found else None
    if not found or not person or not await state.agent_tokens.may_use(person):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing, expired or revoked agent token")
    return Operator(**person.model_dump(), expires_at=found[1])


OperatorDep = Annotated[Operator, Depends(operator)]


def agent_requires(scope: Scope):
    """The same payment.* person scope the matching /v1/payment route checks (apps are checked by `need`)."""

    async def check(person: OperatorDep) -> Operator:
        if not person.has(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {scope}")
        return person

    return Depends(check)
