"""Signing in and out of the dashboard."""

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import PrincipalDep, SettingsDep, get_auth, is_https, safe_next
from app.services.auth import AuthError
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])

SEE_OTHER = status.HTTP_303_SEE_OTHER


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request, settings: SettingsDep, next: str = "/admin"):
    """Sign-in page. Firebase Auth runs in the browser and posts the ID token back."""
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"next": safe_next(next), "firebase": settings.firebase_web_config},
    )


@router.post("/session")
async def start_session(
    request: Request,
    settings: SettingsDep,
    id_token: str = Form(...),
    next: str = Form(default="/admin"),
):
    """Exchange a Firebase ID token for a session cookie.

    A session cookie rather than holding the ID token: ID tokens last an hour and would have
    to be refreshed by JavaScript on every page, which a server-rendered app has no way to do.
    """
    try:
        cookie = await get_auth(request).session_from_login(id_token, settings.session_ttl_days)
    except AuthError as error:
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"next": safe_next(next), "firebase": settings.firebase_web_config, "error": str(error)},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse(safe_next(next), status_code=SEE_OTHER)
    # Path "/" and an optional domain, because other applications read this same cookie.
    # Sharing only works across a common registrable domain — different domains cannot see
    # each other's cookies at all, and there the token has to be passed explicitly.
    response.set_cookie(
        settings.session_cookie_name,
        cookie,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=is_https(request),
        samesite="lax",
        path="/",
        domain=settings.session_cookie_domain or None,
    )
    return response


@router.post("/logout")
async def logout(request: Request, settings: SettingsDep, user: PrincipalDep):
    # Revoked server-side as well as cleared, so a copied cookie dies with the session.
    await get_auth(request).revoke(user.uid)
    response = RedirectResponse("/admin/login", status_code=SEE_OTHER)
    # Same attributes as when it was set, or the browser keeps the original.
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        domain=settings.session_cookie_domain or None,
        secure=is_https(request),
        samesite="lax",
    )
    return response
