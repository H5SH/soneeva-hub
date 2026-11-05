"""/recover: break-glass key rotation of internal tools (karbon-admin) when the admin dashboard is down.

Someone signed in to the admin dashboard (its Auth.js cookie, read with the shared AUTH_SECRET) goes
straight in; otherwise a platform person signs in with email + password and gets a 15-minute cookie
signed with AUTH_SECRET. Every action re-reads their role; rotation needs Tools.Write (`*` has it).
Only tools whose keys are rotated by hand are listed; apps rotate in the admin dashboard.
"""

import hmac
import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.models.identity import Identity
from src.routes.pages import HEADERS, TEMPLATES
from src.services.audit import audit
from src.services.errors import AdminError
from src.services.rate_limit import RateLimit
from src.services.recover_session import (
    COOKIE,
    PATH,
    SESSION_SECONDS,
    csrf_for,
    may_recover,
    own_uid,
    secret_of,
    session,
    sign,
)
from src.services.state import services_of
from src.services.tokens.generate import hub_env_block

router = APIRouter(prefix="/recover", include_in_schema=False)
SIGN_INS = RateLimit(limit=5, window=300)
REFUSED = "Wrong email or password, or no access."


async def _form(request: Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs((await request.body()).decode(errors="replace")).items()}


async def _tools(request: Request) -> list[dict[str, str]]:
    """Tools rotated by hand; one with keys per business (soneeva-voice) rotates by redeploying."""
    return [{"id": t.id, "name": t.name or t.id} for t in await services_of(request).tools.every()
            if not t.subject_keys]


async def _page(request: Request, status: int = 200, person: Identity | None = None,
                **view: Any) -> Response:
    configured = bool(secret_of(request) and (person or services_of(request).identity))
    if person:
        view |= {"person": person, "tools": await _tools(request), "csrf": csrf_for(request, person),
                 "own": bool(own_uid(request))}
    html = TEMPLATES.get_template("recover.html").render(configured=configured, **view)
    return HTMLResponse(html, status, headers=HEADERS)


@router.get("")
async def page(request: Request) -> Response:
    return await _page(request, person=await session(request))


@router.post("/sign-in")
async def sign_in(request: Request) -> Response:
    secret, identity, form = secret_of(request), services_of(request).identity, await _form(request)
    if not (secret and identity):
        return await _page(request, 403)
    email, password = form.get("email", "").strip().lower(), form.get("password", "")
    ip = request.client.host if request.client else "-"
    if not SIGN_INS.allow(f"{ip}|{email}"):
        return await _page(request, 429, error="Too many attempts. Wait a few minutes.", email=email)
    uid = await identity.uid(email, password) if email and password else None
    if not uid or not await may_recover(request, uid):
        return await _page(request, 401, error=REFUSED, email=email)
    expires = str(int(time.time()) + SESSION_SECONDS)
    response = RedirectResponse(PATH, 303, headers=HEADERS)
    settings = services_of(request).settings
    response.set_cookie(COOKIE, f"{uid}.{expires}.{sign(secret, f'{uid}.{expires}')}",
                        max_age=SESSION_SECONDS, path=PATH, httponly=True, samesite="strict",
                        secure=settings.environment != "development")
    return response


async def _checked(request: Request) -> tuple[Identity | None, dict[str, str], Response | None]:
    """The session person and the form, or the page refusing them: no session 401, bad CSRF 403."""
    person, form = await session(request), await _form(request)
    if not person:
        return None, form, await _page(request, 401, error="Signed out. Sign in again.")
    if not hmac.compare_digest(form.get("csrf", "").encode(), csrf_for(request, person).encode()):
        return None, form, await _page(request, 403, person, error="The form expired. Try again.")
    return person, form, None


@router.post("/rotate")
async def rotate(request: Request) -> Response:
    person, form, refused = await _checked(request)
    if refused or not person:
        return refused or Response(status_code=401)
    tool_id = form.get("tool", "")
    if tool_id not in {t["id"] for t in await _tools(request)}:
        return await _page(request, 400, person, error=f"{tool_id or 'That tool'} is not rotated here.")
    try:
        revoke = form.get("revoke") == "on"
        kid, private_pem = await services_of(request).tools.rotate_key(tool_id, revoke)
    except AdminError as error:
        return await _page(request, 400, person, error=str(error))
    await audit(services_of(request).db, "key.rotate", "-", person.uid,
                {"toolId": tool_id, "kid": kid, "via": "recover", "revoke": revoke})
    block = hub_env_block(tool_id, kid, private_pem)
    return await _page(request, person=person, rotated={"tool": tool_id, "kid": kid, "env": block,
                                                       "revoked": revoke})


@router.post("/sign-out")
async def sign_out(request: Request) -> Response:
    """Ends only the /recover cookie; an admin dashboard session stays."""
    _, _, refused = await _checked(request)
    if refused:
        return refused
    response = RedirectResponse(PATH, 303, headers=HEADERS)
    response.delete_cookie(COOKIE, path=PATH)
    return response
