"""/recover: break-glass key rotation of internal tools (karbon-admin) when the admin dashboard is down.

Someone signed in to the admin dashboard (its Auth.js cookie, read with the shared AUTH_SECRET) goes
straight in; otherwise a platform person signs in with email + password and gets a 15-minute cookie
signed with AUTH_SECRET. Every action re-reads their role; rotation needs Tools.Write (`*` has it).
Only tools whose keys are rotated by hand are listed; apps rotate in the admin dashboard.
"""

import hashlib
import hmac
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader

from src.auth.admin_session import admin_session
from src.models.admin import Principal
from src.services.businesses import audit
from src.services.errors import AdminError
from src.services.people.rules import holds
from src.services.rate_limit import RateLimit
from src.services.tokens.generate import hub_env_block
from src.services.tokens.staff import find_principal

router = APIRouter(prefix="/recover", include_in_schema=False)
TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "templates"), autoescape=True)
COOKIE, PATH, SESSION_SECONDS = "recover", "/", 15 * 60
SIGN_INS = RateLimit(limit=5, window=300)
REFUSED = "Wrong email or password, or no access."
HEADERS = {
    "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; script-src 'unsafe-inline'; form-action 'self'; "
    "frame-ancestors 'none'",
}


def _secret(request: Request) -> str:
    settings = request.app.state.settings
    return settings.auth_secret if settings else ""


def _sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _csrf(request: Request, person: Principal) -> str:
    """Bound to the /recover cookie, or to the person when they came in through the admin dashboard."""
    return _sign(_secret(request), f"csrf.{request.cookies.get(COOKIE) or 'admin.' + person.uid}")


async def _form(request: Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs((await request.body()).decode(errors="replace")).items()}


async def _allowed(request: Request, uid: str) -> Principal | None:
    """Enabled, with Tools.Write (also through `*`)."""
    state = request.app.state
    person = await find_principal(state, uid)
    return person if person and holds(await state.roles.raw(person.role), "Tools.Write") else None


def _own(request: Request) -> str | None:
    """The uid of a valid /recover cookie."""
    secret, cookie = _secret(request), request.cookies.get(COOKIE, "")
    uid, _, rest = cookie.partition(".")
    expires, _, signature = rest.partition(".")
    if not (secret and uid and expires.isdigit() and int(expires) > time.time()):
        return None
    return uid if hmac.compare_digest(signature, _sign(secret, f"{uid}.{expires}")) else None


async def session(request: Request) -> Principal | None:
    """Staff: signed in here or to the admin dashboard, and holding Tools.Write right now."""
    uid = _own(request) or admin_session(request)
    return await _allowed(request, uid) if uid else None


async def _tools(request: Request) -> list[dict[str, str]]:
    """Tools rotated by hand; one with keys per business (soneeva-voice) rotates by redeploying."""
    return [{"id": t.id, "name": t.name or t.id} for t in await request.app.state.tools.every()
            if not t.subject_keys]


async def _page(request: Request, status: int = 200, person: Principal | None = None,
                **view: Any) -> Response:
    configured = bool(_secret(request) and (person or request.app.state.identity))
    if person:
        view |= {"person": person, "tools": await _tools(request), "csrf": _csrf(request, person),
                 "own": bool(_own(request))}
    html = TEMPLATES.get_template("recover.html").render(configured=configured, **view)
    return HTMLResponse(html, status, headers=HEADERS)


@router.get("")
async def page(request: Request) -> Response:
    return await _page(request, person=await session(request))


@router.post("/sign-in")
async def sign_in(request: Request) -> Response:
    secret, identity, form = _secret(request), request.app.state.identity, await _form(request)
    if not (secret and identity):
        return await _page(request, 403)
    email, password = form.get("email", "").strip().lower(), form.get("password", "")
    ip = request.client.host if request.client else "-"
    if not SIGN_INS.allow(f"{ip}|{email}"):
        return await _page(request, 429, error="Too many attempts. Wait a few minutes.", email=email)
    uid = await identity.uid(email, password) if email and password else None
    if not uid or not await _allowed(request, uid):
        return await _page(request, 401, error=REFUSED, email=email)
    expires = str(int(time.time()) + SESSION_SECONDS)
    response = RedirectResponse(PATH, 303, headers=HEADERS)
    settings = request.app.state.settings
    response.set_cookie(COOKIE, f"{uid}.{expires}.{_sign(secret, f'{uid}.{expires}')}",
                        max_age=SESSION_SECONDS, path=PATH, httponly=True, samesite="strict",
                        secure=settings.environment != "development")
    return response


async def _checked(request: Request) -> tuple[Principal | Response, dict[str, str]]:
    """The session person and the form, or the refusal: no session 401, bad CSRF 403."""
    person, form = await session(request), await _form(request)
    if not person:
        return await _page(request, 401, error="Signed out. Sign in again."), form
    if not hmac.compare_digest(form.get("csrf", ""), _csrf(request, person)):
        return await _page(request, 403, person, error="The form expired. Try again."), form
    return person, form


@router.post("/rotate")
async def rotate(request: Request) -> Response:
    person, form = await _checked(request)
    if isinstance(person, Response):
        return person
    tool_id = form.get("tool", "")
    if tool_id not in {t["id"] for t in await _tools(request)}:
        return await _page(request, 400, person, error=f"{tool_id or 'That tool'} is not rotated here.")
    try:
        kid, private_pem = await request.app.state.tools.rotate_key(tool_id)
    except AdminError as error:
        return await _page(request, 400, person, error=str(error))
    await audit(request.app.state.db, "key.rotate", "-", person.uid,
                {"toolId": tool_id, "kid": kid, "via": "recover"})
    block = hub_env_block(tool_id, kid, private_pem)
    return await _page(request, person=person, rotated={"tool": tool_id, "kid": kid, "env": block})


@router.post("/sign-out")
async def sign_out(request: Request) -> Response:
    """Ends only the /recover cookie; an admin dashboard session stays."""
    person, _ = await _checked(request)
    if isinstance(person, Response):
        return person
    response = RedirectResponse(PATH, 303, headers=HEADERS)
    response.delete_cookie(COOKIE, path=PATH)
    return response
