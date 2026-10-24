"""/v1/demo/settings: the phone demo's settings, saved tab by tab, each by its own scope (SECTION_SCOPES);
Ivr.View or any of those reads them. `demo/history` keeps each section's previous version and who saved it,
so a save can be reverted. The demo service follows `demo/settings` live and has no defaults."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from src.models.admin import Scope
from src.models.db import DEMO_HISTORY, DEMO_SETTINGS, Default
from src.models.demo import SECTION_MODELS, SECTION_SCOPES, DemoSettings, Section
from src.models.people import Actor
from src.services.businesses import audit
from src.services.errors import PlatformError
from src.services.people.access import requires_actor
from src.services.people.rules import holds

router = APIRouter(prefix="/v1/demo", tags=["demo"])
VIEW = "Ivr.View"


def _need(actor: Actor, *any_of: str) -> None:
    if not actor.platform or not any(holds(actor.scopes, s) for s in any_of):
        raise PlatformError(f"Requires {' or '.join(any_of)}", 403)


async def _read(request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    db = request.app.state.db
    row = await db.get_row(Default.DEMO, DEMO_SETTINGS)
    if not row:
        raise PlatformError("No demo settings in the database", 404)
    settings = DemoSettings(**{k: v for k, v in row.items() if k in DemoSettings.model_fields})
    return settings.model_dump(), await db.get_row(Default.DEMO, DEMO_HISTORY) or {}


def _shown(settings: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    """The settings, and per section who saved it last and whether there is a version to go back to."""
    saved = {s: {"by": (history.get(s) or {}).get("by"), "at": (history.get(s) or {}).get("at"),
                 "revertible": bool((history.get(s) or {}).get("previous"))} for s in SECTION_MODELS}
    return {"settings": settings, "saved": saved}


async def _save(request: Request, actor: Actor, section: str, value: dict[str, Any]) -> dict[str, Any]:
    settings, history = await _read(request)
    previous = settings[section]
    settings[section] = value
    DemoSettings(**settings)
    db = request.app.state.db
    await db.set_row(Default.DEMO, DEMO_SETTINGS, settings)
    entry = {"previous": previous, "by": actor.uid, "at": datetime.now(UTC).isoformat()}
    await db.set_row(Default.DEMO, DEMO_HISTORY, {section: entry})
    await audit(db, f"demo.{section}", "", actor.uid, {})
    return _shown(settings, history | {section: entry})


@router.get("/settings")
async def get_settings(request: Request, actor: Actor = requires_actor(Scope.HUB_DEMO_VIEW)) -> dict:
    _need(actor, VIEW, *SECTION_SCOPES.values())
    return _shown(*await _read(request))


@router.put("/settings/{section}")
async def put_section(
    section: Section, request: Request, body: dict[str, Any] = Body(...),
    actor: Actor = requires_actor(Scope.HUB_DEMO_UPDATE),
) -> dict:
    """One tab, whole; the demo picks it up at once."""
    _need(actor, SECTION_SCOPES[section])
    try:
        value = SECTION_MODELS[section](**body).model_dump()
    except ValidationError as error:
        raise RequestValidationError(error.errors(include_url=False)) from error
    return await _save(request, actor, section, value)


@router.post("/settings/{section}/revert")
async def revert_section(
    section: Section, request: Request, actor: Actor = requires_actor(Scope.HUB_DEMO_UPDATE),
) -> dict:
    """Back to the version before the last save (which becomes the one to go back to)."""
    _need(actor, SECTION_SCOPES[section])
    _, history = await _read(request)
    previous = (history.get(section) or {}).get("previous")
    if not previous:
        raise PlatformError("Nothing to go back to", 409)
    return await _save(request, actor, section, previous)
