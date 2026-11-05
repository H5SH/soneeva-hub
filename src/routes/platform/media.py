"""/v1/media/{path}: private images and recordings at fixed paths, checked against src/services/media.py."""

from email.utils import format_datetime

from fastapi import APIRouter, Request, Response

from src.dependencies import BusinessDep, IvrDep, MediaDep, StorageDep
from src.models.admin import Scope
from src.models.identity import Identity
from src.services.errors import PlatformError
from src.services.media import audio_target, check
from src.services.people.access import requires_actor
from src.services.state import services_of
from src.services.tokens.staff import with_payment
from src.services.uploads import read_file

router = APIRouter(prefix="/v1/media", tags=["media"])


async def _who(request: Request, actor: Identity) -> Identity:
    state = services_of(request)
    return actor if actor.business_id else await with_payment(state.db, state.roles, actor)


def _quoted(etag: str) -> str:
    return etag if etag.startswith(('"', 'W/"')) else f'"{etag}"'


def _matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = {t.strip().removeprefix("W/") for t in header.split(",")}
    return "*" in tags or etag.removeprefix("W/") in tags


@router.get("/{path:path}")
async def get_media(
    path: str, request: Request, rules: MediaDep, files: StorageDep,
    actor: Identity = requires_actor(Scope.HUB_MEDIA_VIEW),
) -> Response:
    await check(rules, await _who(request, actor), path, edit=False)
    found = await files.get(path)
    if not found:
        raise PlatformError("No such file", 404)
    etag = _quoted(found.etag)
    headers = {"ETag": etag, "Last-Modified": format_datetime(found.updated, usegmt=True),
               "Cache-Control": "private, no-cache"}
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(found.data, media_type=found.content_type, headers=headers)


@router.put("/{path:path}")
async def put_media(
    path: str, request: Request, rules: MediaDep, files: StorageDep,
    actor: Identity = requires_actor(Scope.HUB_MEDIA_UPDATE),
) -> dict:
    await check(rules, await _who(request, actor), path, edit=True)
    if audio_target(path):
        raise PlatformError("Recordings are made by the voice service: save the text instead", 405)
    data, kind = await read_file(request)
    updated = await files.put(path, data, kind)
    return {"path": path, "updatedAt": updated.isoformat()}


@router.delete("/{path:path}")
async def delete_media(
    path: str, request: Request, rules: MediaDep, files: StorageDep, businesses: BusinessDep,
    ivr: IvrDep, actor: Identity = requires_actor(Scope.HUB_MEDIA_UPDATE),
) -> dict:
    """A recording goes through its own rules: an old version, or the whole thing while unused."""
    await check(rules, await _who(request, actor), path, edit=True)
    target = audio_target(path)
    if not target:
        await files.delete(path)
        return {"deleted": True}
    business_id, name, version = target
    business = await businesses.require(None, business_id)
    if version:
        await ivr.delete_version(business, name, version)
    else:
        await ivr.delete_audio(business, name)
    return {"deleted": True}
