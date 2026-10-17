"""`scopes/{appId}`: which scope ids exist. What they mean is in each app's scopes.json."""

import json
from pathlib import Path
from typing import Any

from src.db import Database, Query, Write
from src.models.admin import DEFAULT_ROLES, Scope, person_scopes
from src.models.db import DELETED_FLAG, Default
from src.services.errors import AdminError
from src.tunables import tunable

HUB_APP = "karbon-hub"


async def save_catalogue(db: Database, app_id: str, scopes: list[dict[str, Any]]) -> None:
    await db.set_row(Default.SCOPES, app_id, {"scopes": scopes})


def read_console_scopes(path: Path) -> list[dict[str, str]]:
    """The business scopes of a console `scopes.json`: `{business: [{group, scopes: [{id}]}]}`."""
    try:
        groups = json.loads(path.read_text())["business"]
        return [{"id": s["id"], "group": g["group"]} for g in groups for s in g["scopes"]]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise AdminError(f"Not a console scopes.json: {path} ({error})") from error


async def seed_hub(db: Database) -> None:
    """karbon-hub's own catalogue, always in step with the code; default roles only once."""
    await save_catalogue(db, HUB_APP, [{"id": str(s), "group": s.group} for s in Scope])
    await seed_platform_roles(db)


async def seed_platform_roles(db: Database) -> list[str]:
    """Creates each missing platform role with its default scopes (person scopes and `platformRoles`);
    an existing role is the owner's, so only its `hub.*` ids (app scopes, never a person's) are dropped.
    `*` covers new console scopes by itself. Returns the roles written."""
    console = tunable("platformRoles")
    stored = {d.id: d.data for d in await db.query(Query(Default.ROLES)) if not d.data.get(DELETED_FLAG)}
    written = []
    for role in sorted(DEFAULT_ROLES.keys() | console.keys() | stored.keys()):
        if role not in stored:
            defaults = [str(s) for s in DEFAULT_ROLES.get(role, [])] + console.get(role, [])
            await db.set_row(Default.ROLES, role, {"scopes": defaults})
        else:
            raw = stored[role].get("scopes")
            if (scopes := person_scopes(raw)) == raw:
                continue
            await db.batch([Write(Default.ROLES, role, {"scopes": scopes}, merge=True)])
        written.append(role)
    return written
