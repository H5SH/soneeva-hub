"""Internal tools (`tools/{id}`): hub keys and scopes for our own callers, never a Stripe link."""

import re
from typing import Any

from src.db import Database
from src.models.auth import Audience, PublicKey, Tool
from src.models.db import Default, to_doc
from src.models.mode import STAGING
from src.services.errors import AdminError, PlatformError
from src.services.tokens.generate import rotate, subject_key
from src.services.tokens.keys import KeyStore

CALLER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
#: The hub's own name (a token's `aud`): never an app or a tool.
RESERVED = {Audience.HUB.value}


async def check_new_id(db: Database, caller_id: str) -> None:
    """Apps and tools share one id space (a token's `iss`); `_staging` is every app's test twin."""
    if not CALLER_ID.match(caller_id) or caller_id.endswith(STAGING):
        raise AdminError(f"Use lowercase letters, numbers, hyphens and underscores, not ending in {STAGING}.")
    if caller_id in RESERVED:
        raise AdminError(f"'{caller_id}' is reserved.")
    if await db.get_row(Default.APPS, caller_id) or await db.get_row(Default.TOOLS, caller_id):
        raise AdminError(f"'{caller_id}' is already an app or a tool.")


class ToolService:
    def __init__(self, db: Database, keys: KeyStore) -> None:
        self._db = db
        self._keys = keys

    async def every(self) -> list[Tool]:
        return sorted((Tool(**row) for row in await self._db.get_rows(Default.TOOLS)), key=lambda t: t.id)

    async def get(self, tool_id: str) -> Tool | None:
        row = await self._db.get_row(Default.TOOLS, tool_id)
        return Tool(**(row | {"id": tool_id})) if row else None

    async def require(self, tool_id: str) -> Tool:
        tool = await self.get(tool_id)
        if not tool:
            raise PlatformError(f"No tool '{tool_id}'", 404)
        return tool

    async def create(self, tool_id: str, name: str, scopes: list[str], subject_keys: bool = False) -> Tool:
        await check_new_id(self._db, tool_id)
        await self._save(tool_id, {"name": name or tool_id, "status": "active", "keys": [], "scopes": scopes,
                                   "subject_keys": subject_keys})
        return await self.require(tool_id)

    async def update(self, tool_id: str, **fields: Any) -> Tool:
        await self.require(tool_id)
        await self._save(tool_id, fields)
        return await self.require(tool_id)

    async def delete(self, tool_id: str) -> None:
        await self.require(tool_id)
        await self._db.delete_row(Default.TOOLS, tool_id)
        self._keys.forget(tool_id)

    async def rotate_key(self, tool_id: str) -> tuple[str, str]:
        """Returns (kid, private PEM), shown once. A tool with keys per business rotates by redeploying."""
        tool = await self.require(tool_id)
        if tool.subject_keys:
            raise AdminError(f"'{tool_id}' has a key per business; redeploying rotates it.")
        keys, kid, private_pem = rotate(tool.keys)
        await self._save_keys(tool_id, keys)
        return kid, private_pem

    async def issue_subject_key(self, tool_id: str, subject: str) -> tuple[str, str]:
        """A key that only signs for `subject` (one business's voice), replacing its previous one."""
        tool = await self.get(tool_id)
        if not tool or not tool.subject_keys:
            raise AdminError(f"No tool '{tool_id}' with keys per business. Run seed:soneeva first.")
        keys, kid, private_pem = subject_key(tool.keys, subject)
        await self._save_keys(tool_id, keys)
        return kid, private_pem

    async def _save_keys(self, tool_id: str, keys: list[PublicKey]) -> None:
        await self._save(tool_id, {"keys": [k.model_dump(mode="json") for k in keys]})

    async def _save(self, tool_id: str, fields: dict[str, Any]) -> None:
        await self._db.set_row(Default.TOOLS, tool_id, to_doc(fields))
        self._keys.forget(tool_id)
