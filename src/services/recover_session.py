"""Who is signed in to /recover: its own 15-minute cookie, signed with AUTH_SECRET, or the admin dashboard's
session. Either way they must hold Tools.Write right now."""

import hashlib
import hmac
import time

from fastapi import Request

from src.auth.admin_session import admin_session
from src.models.identity import Identity
from src.services.people.rules import holds
from src.services.state import services_of
from src.services.tokens.staff import find_principal

COOKIE, PATH, SESSION_SECONDS = "recover", "/", 15 * 60


def secret_of(request: Request) -> str:
    return services_of(request).settings.auth_secret


def sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def csrf_for(request: Request, person: Identity) -> str:
    """Bound to the /recover cookie, or to the person when they came in through the admin dashboard."""
    return sign(secret_of(request), f"csrf.{request.cookies.get(COOKIE) or 'admin.' + person.uid}")


async def may_recover(request: Request, uid: str) -> Identity | None:
    """Enabled, with Tools.Write (also through `*`)."""
    state = services_of(request)
    person = await find_principal(state.db, state.roles, uid)
    return person if person and holds(await state.roles.raw(person.role), "Tools.Write") else None


def own_uid(request: Request) -> str | None:
    """The uid of a valid /recover cookie."""
    secret, cookie = secret_of(request), request.cookies.get(COOKIE, "")
    uid, _, rest = cookie.partition(".")
    expires, _, signature = rest.partition(".")
    if not (secret and uid and expires.isdigit() and int(expires) > time.time()):
        return None
    return uid if hmac.compare_digest(signature, sign(secret, f"{uid}.{expires}")) else None


async def session(request: Request) -> Identity | None:
    """Staff: signed in here or to the admin dashboard, and holding Tools.Write right now."""
    uid = own_uid(request) or admin_session(request)
    return await may_recover(request, uid) if uid else None
