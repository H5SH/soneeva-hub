"""`Authorization: Bearer kat_…` for /v1/agent: the person behind an operator token, re-read per call."""

from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from src.models.admin import Scope
from src.models.identity import Identity
from src.services.state import services_of
from src.services.tokens.staff import find_principal


class Operator(Identity):
    expires_at: datetime


async def operator(request: Request) -> Operator:
    """401 for a missing, unknown, expired or revoked token, a disabled person, or one whose role no
    longer holds Agent.Use."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    state = services_of(request)
    refused = HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing, expired or revoked agent token")
    found = await state.agent_tokens.resolve(token.strip()) if scheme.lower() == "bearer" else None
    if not found:
        raise refused
    uid, expires_at = found
    person = await find_principal(state.db, state.roles, uid)
    if not person or not await state.agent_tokens.may_use(person):
        raise refused
    return Operator(**person.model_dump(), expires_at=expires_at)


OperatorDep = Annotated[Operator, Depends(operator)]


def agent_requires(scope: Scope):
    """The same payment.* person scope the matching /v1/payment route checks (apps are checked by `need`)."""

    async def check(person: OperatorDep) -> Operator:
        if not person.has(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {scope}")
        return person

    return Depends(check)
