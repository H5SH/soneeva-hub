"""/v1/knowledge/read: a photo or PDF of a menu or other business document, read by Gemini into an
agent's knowledge (karbon-admin's demo steps, as text) or a business's menu (the console, as rows).
Nothing is stored: the result is a draft the person checks and saves the usual way."""

from typing import Literal

from fastapi import APIRouter, Request

from src.dependencies import ReaderDep
from src.models.admin import Scope
from src.models.demo import SECTION_SCOPES
from src.models.people import Actor
from src.routes.platform.media import read_file
from src.services.errors import PlatformError
from src.services.people.access import requires_actor
from src.services.people.rules import holds

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
#: Who may: in a business, whoever edits its menu; on the platform, whoever edits a demo line.
BUSINESS = ("menu.edit",)
PLATFORM = (SECTION_SCOPES["soneevaNumber"], SECTION_SCOPES["demoNumber"])


@router.post("/read")
async def read(
    request: Request, reader: ReaderDep, form: Literal["text", "menu"] = "text",
    actor: Actor = requires_actor(Scope.HUB_KNOWLEDGE_READ),
) -> dict:
    """The raw file as the body (its Content-Type one of `knowledge.types`): `{text}` or `{items}`."""
    needed = BUSINESS if actor.business_id else PLATFORM if actor.platform else ()
    if not any(holds(actor.scopes, s) for s in needed):
        raise PlatformError(f"Requires {' or '.join(needed) or 'a business or platform person'}", 403)
    data, kind = await read_file(request, "knowledge")
    if form == "menu":
        return {"items": [row.model_dump() for row in await reader.menu(data, kind)]}
    return {"text": await reader.text(data, kind)}
