from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import AdminDep, get_auth, requires
from app.models.admin import Principal, Scope
from app.services.admin import AdminError
from app.services.auth import AuthError
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])
SEE_OTHER = status.HTTP_303_SEE_OTHER


@router.get("/users", response_class=HTMLResponse)
async def people(
    request: Request, admin: AdminDep, ok: str = "", err: str = "", link: str = "",
    user: Principal = requires(Scope.USER_VIEW),
):
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "user": user,
            "nav": "users",
            "people": await admin.list_users(),
            "link": link,
            "roles": await admin.list_roles(),
            "apps": await admin.list_apps(user),
            "ok": ok,
            "err": err,
            "Scope": Scope,
        },
    )


@router.post("/users")
async def create_person(
    request: Request,
    admin: AdminDep,
    email: Annotated[str, Form()],
    role: Annotated[str, Form()] = "",
    apps: Annotated[list[str], Form()] = [],  # noqa: B006
    user: Principal = requires(Scope.USER_INVITE),
):
    """Create the account and grant it a role in one step.

    The password is never chosen here — the account is created with a random one and the
    operator is handed a link for the person to set their own.
    """
    try:
        uid, link = await get_auth(request).create_user(email)
    except AuthError as error:
        return RedirectResponse(f"/admin/users?err={error}", status_code=SEE_OTHER)

    if role:
        await admin.save_user(uid, role.strip(), apps, False)

    query = urlencode({"ok": f"{email.strip().lower()} created.", "link": link})
    return RedirectResponse(f"/admin/users?{query}", status_code=SEE_OTHER)


@router.post("/users/{uid}/delete")
async def delete_person(
    request: Request, uid: str, user: Principal = requires(Scope.USER_DELETE)
):
    if uid == user.uid:
        return RedirectResponse(
            "/admin/users?err=You+cannot+remove+your+own+account.", status_code=SEE_OTHER
        )
    try:
        await get_auth(request).delete_user(uid)
    except AuthError as error:
        return RedirectResponse(f"/admin/users?err={error}", status_code=SEE_OTHER)
    return RedirectResponse("/admin/users?ok=Account+removed.", status_code=SEE_OTHER)


@router.post("/users/{uid}")
async def save_person(
    uid: str,
    admin: AdminDep,
    role: Annotated[str, Form()] = "",
    apps: Annotated[list[str], Form()] = [],  # noqa: B006 - FastAPI reads the default
    disabled: Annotated[str, Form()] = "",
    user: Principal = requires(Scope.USER_UPDATE),
):
    """Grant a role and the apps it applies to.

    A superadmin cannot remove their own role here — locking the last one out would leave the
    dashboard unreachable, and the only way back would be the seed script.
    """
    if uid == user.uid and role != user.role:
        return RedirectResponse(
            "/admin/users?err=You+cannot+change+your+own+role.", status_code=SEE_OTHER
        )
    try:
        await admin.save_user(uid, role.strip(), apps, disabled == "on")
    except AdminError as error:
        return RedirectResponse(f"/admin/users?err={error}", status_code=SEE_OTHER)
    return RedirectResponse("/admin/users?ok=Saved.", status_code=SEE_OTHER)


@router.get("/roles", response_class=HTMLResponse)
async def roles(
    request: Request, admin: AdminDep, ok: str = "", err: str = "",
    user: Principal = requires(Scope.USER_VIEW),
):
    return templates.TemplateResponse(
        request,
        "admin/roles.html",
        {
            "user": user,
            "nav": "roles",
            "roles": await admin.list_roles(),
            "scopes": [str(s) for s in Scope],
            "holders": await admin.role_holders(),
            "ok": ok,
            "err": err,
            "Scope": Scope,
        },
    )


@router.post("/roles")
async def create_role(
    admin: AdminDep,
    name: Annotated[str, Form()],
    scopes: Annotated[list[str], Form()] = [],  # noqa: B006
    user: Principal = requires(Scope.USER_UPDATE),
):
    valid = {str(s) for s in Scope}
    try:
        created = await admin.create_role(name, [s for s in scopes if s in valid])
    except AdminError as error:
        return RedirectResponse(f"/admin/roles?err={error}", status_code=SEE_OTHER)
    return RedirectResponse(f"/admin/roles?ok=Role+{created}+created.", status_code=SEE_OTHER)


@router.post("/roles/{role}/delete")
async def delete_role(
    role: str, admin: AdminDep, user: Principal = requires(Scope.USER_UPDATE)
):
    try:
        await admin.delete_role(role)
    except AdminError as error:
        return RedirectResponse(f"/admin/roles?err={error}", status_code=SEE_OTHER)
    return RedirectResponse(f"/admin/roles?ok=Role+{role}+removed.", status_code=SEE_OTHER)


@router.post("/roles/{role}")
async def save_role(
    role: str,
    admin: AdminDep,
    scopes: Annotated[list[str], Form()] = [],  # noqa: B006
    user: Principal = requires(Scope.USER_UPDATE),
):
    """Roles are data, so a new one needs no deploy — only scopes that exist are accepted."""
    valid = {str(s) for s in Scope}
    await admin.save_role(role.strip(), [s for s in scopes if s in valid])
    return RedirectResponse(f"/admin/roles?ok=Role+{role}+saved.", status_code=SEE_OTHER)
