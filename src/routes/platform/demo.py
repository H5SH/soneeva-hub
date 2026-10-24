"""/v1/demo/settings: the demo's settings, read with Ivr.View or Demo.Settings, changed with Demo.Settings.
The demo service follows the document live; it has no defaults, so a change is all it ever runs on."""

from fastapi import APIRouter, Request

from src.models.admin import Scope
from src.models.db import DEMO_SETTINGS, Default
from src.models.demo import DemoSettings
from src.models.people import Actor
from src.services.businesses import audit
from src.services.errors import PlatformError
from src.services.people.access import requires_actor
from src.services.people.rules import holds

router = APIRouter(prefix="/v1/demo", tags=["demo"])
VIEW, EDIT = "Ivr.View", "Demo.Settings"


def _need(actor: Actor, *any_of: str) -> None:
    if not actor.platform or not any(holds(actor.scopes, s) for s in any_of):
        raise PlatformError(f"Requires {' or '.join(any_of)}", 403)


@router.get("/settings")
async def get_settings(request: Request, actor: Actor = requires_actor(Scope.HUB_DEMO_VIEW)) -> dict:
    _need(actor, VIEW, EDIT)
    row = await request.app.state.db.get_row(Default.DEMO, DEMO_SETTINGS)
    if not row:
        raise PlatformError("No demo settings yet: `demo:settings:import <file>`", 404)
    return DemoSettings(**{k: v for k, v in row.items() if k in DemoSettings.model_fields}).model_dump()


@router.put("/settings")
async def put_settings(
    body: DemoSettings, request: Request, actor: Actor = requires_actor(Scope.HUB_DEMO_UPDATE),
) -> dict:
    """The whole document; the demo picks it up at once."""
    _need(actor, EDIT)
    db = request.app.state.db
    await db.set_row(Default.DEMO, DEMO_SETTINGS, body.model_dump())
    await audit(db, "demo.settings", "", actor.uid, {})
    return body.model_dump()
