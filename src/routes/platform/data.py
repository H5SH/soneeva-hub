"""/v1/scopes (a console's scope ids), /v1/platform/calls (the browser demo's calls) and
/v1/platform/home (karbon-admin's home page)."""

from fastapi import APIRouter, Depends, Query, Request

from src.dependencies import HomeDep, PlatformDataDep
from src.models.admin import Scope
from src.models.people import Actor
from src.models.platform import Caller
from src.services.people.access import requires_actor, requires_app
from src.services.platform.calls import CallRange
from src.services.platform.home import Viewer
from src.services.platform.records import parse_iso
from src.services.tokens.caller import person
from src.services.tokens.staff import find_principal
from src.tunables import tunable

router = APIRouter(prefix="/v1", tags=["platform data"])
VIEW = Scope.HUB_PLATFORM_VIEW
ENGINE = Query(default=None, pattern="^(spark|smart)$")


@router.get("/scopes/{app_id}")
async def scope_catalogue(
    app_id: str, data: PlatformDataDep, caller: Caller = requires_app(VIEW, Scope.HUB_BUSINESS_VIEW)
) -> dict:
    """Scope ids only, nothing about anyone: the platform view or the console's business view."""
    return {"scopes": await data.scopes(app_id)}


@router.get("/platform/calls")
async def list_calls(
    data: PlatformDataDep, scope: str = Query(max_length=60),
    caller: str | None = Query(default=None, max_length=80),
    since: str | None = Query(default=None, alias="from"),
    until: str | None = Query(default=None, alias="to"),
    before: str | None = None, limit: int = Query(default=50, ge=1), engine: str | None = ENGINE,
    actor: Actor = requires_actor(VIEW),
) -> dict:
    """`engine`: only Spark or only Smart calls."""
    span = CallRange(since=parse_iso(since), until=parse_iso(until), before=parse_iso(before))
    return await data.calls(actor, scope, caller, span, min(limit, tunable("people.callsPageMax")), engine)


@router.get("/platform/calls/totals")
async def call_totals(
    data: PlatformDataDep, scope: str = Query(max_length=60), callers: str | None = Query(default=None),
    actor: Actor = requires_actor(VIEW),
) -> dict:
    asked = [c for c in callers.split(",") if c] if callers is not None else None
    return await data.totals(actor, scope, asked)


@router.get("/platform/calls/daily")
async def daily_calls(
    data: PlatformDataDep, scope: str = Query(max_length=60),
    caller: str | None = Query(default=None, max_length=80),
    since: str | None = Query(default=None, alias="from"),
    until: str | None = Query(default=None, alias="to"), engine: str | None = ENGINE,
    actor: Actor = requires_actor(VIEW),
) -> dict:
    """Per UTC day (default: the last `people.callDays`): calls, minutes and cost by part (`live` is
    Spark's), of only Spark or only Smart calls with `engine`."""
    span = CallRange(since=parse_iso(since), until=parse_iso(until))
    return {"days": await data.daily(actor, scope, caller, span, engine)}


@router.get("/platform/home")
async def platform_home(
    request: Request, home: HomeDep, days: int | None = Query(default=None, ge=1, le=366),
    caller: Caller = Depends(person),
) -> dict:
    """The sections the person may see, each over what they reach; the others are left out. No revenue."""
    actor = Actor(app_id=caller.app_id, sub=caller.sub, uid=caller.uid or "", platform=True,
                  scopes=caller.person_scopes or [])
    principal = await find_principal(request.app.state, actor.uid)
    return await home.home(Viewer(caller, actor, principal), days or tunable("people.callDays"))


@router.get("/platform/calls/{caller}/{call_id}")
async def get_call(
    caller: str, call_id: str, data: PlatformDataDep, scope: str = Query(max_length=60),
    actor: Actor = requires_actor(VIEW),
) -> dict:
    return await data.call(actor, scope, caller, call_id)


@router.get("/platform/calls/{caller}/{call_id}/messages")
async def call_messages(
    caller: str, call_id: str, data: PlatformDataDep, scope: str = Query(max_length=60),
    after: int | None = None, actor: Actor = requires_actor(VIEW),
) -> dict:
    return {"messages": await data.messages(actor, scope, caller, call_id, after)}
