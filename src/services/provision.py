"""Provisioning a signed-up business: where its data goes (src/db/place.py) made ready, its indexes,
seed rows and the owner's claims. A database of its own, or a region's shared one, is made on first
use; nested data needs the shared database's NESTED_INDEXES. It is `provisioned` only once the indexes
(src/db/indexes.py) are built, so the console never queries without them; when they take longer than
`businesses.indexWaitSeconds` the retry waits again.

Sign-up answers as soon as the account and the pending business row exist; this runs afterwards (a
background task, `POST /v1/businesses/{id}/provision` or `business:provision`). Every step is
idempotent, so an interrupted or failed run is simply run again. `provisionError` keeps the last failure.
"""

import asyncio
import logging
from typing import Any

from src.auth import AccountProvider
from src.db import BUSINESS_INDEXES, NESTED_INDEXES, Database, business_db, place
from src.db.indexes import DbIndex
from src.models.db import AGENT_SETTINGS, CONSOLE_SETTINGS, BusinessDb, Default
from src.models.platform import Business
from src.services.checklist import ChecklistService
from src.services.errors import PlatformError
from src.services.regions import region_location
from src.tunables import tunable

logger = logging.getLogger(__name__)
ERROR_LENGTH = 200
DEFAULT_DATABASE = "(default)"


def _indexed(business: Business) -> tuple[str, tuple[DbIndex, ...]]:
    """The database holding the business's data, and the indexes it needs there."""
    where = place(business.id, business.dbId)
    return where.database or DEFAULT_DATABASE, NESTED_INDEXES if where.root else BUSINESS_INDEXES


class Provisioner:
    def __init__(self, db: Database, accounts: AccountProvider, checklist: ChecklistService) -> None:
        self._db, self._accounts, self._checklist = db, accounts, checklist
        self._running: set[str] = set()

    async def run(self, business_id: str) -> Business:
        """Provisions the business unless it is already running here; raises on failure. A provisioned
        one only gets its indexes ensured (cheap once built), e.g. one made before an index was added."""
        row = await self._db.get_row(Default.BUSINESSES, business_id)
        if not row:
            raise PlatformError("No such business", 404)
        business = Business(**row)
        if business_id in self._running:
            return business
        self._running.add(business_id)
        try:
            if business.provisioned:
                await self._indexes(business)
            else:
                await self._build(business, row.get("signup") or {})
        except Exception as error:
            message = (str(error) or type(error).__name__)[:ERROR_LENGTH]
            await self._db.set_row(Default.BUSINESSES, business_id, {"provisionError": message})
            raise
        finally:
            self._running.discard(business_id)
        done = {"provisioned": True, "provisionError": None}
        await self._db.set_row(Default.BUSINESSES, business_id, done)
        return business.model_copy(update=done)

    async def quietly(self, business_id: str) -> None:
        """For background tasks: a failure is logged and kept as `provisionError`, never raised."""
        try:
            await self.run(business_id)
        except Exception:  # noqa: BLE001 - recorded on the business for the API
            logger.exception("provisioning failed for %s", business_id)

    async def _build(self, business: Business, signup: dict[str, Any]) -> None:
        await self._database(business)
        await self._db.request_indexes(*_indexed(business))
        owner = await self._accounts.get(business.ownerUid)
        if not owner:
            raise PlatformError("The owner's account no longer exists", 409)
        bdb = business_db(self._db, business.id, business.dbId)
        catalogue = await self._catalogue(business.appId)
        roles = [bdb.set_row(BusinessDb.ROLES, role, {
            "name": row["name"], "scopes": catalogue if "*" in row["scopes"] else row["scopes"],
        }) for role, row in tunable("businesses.roles").items()]
        await asyncio.gather(
            *roles,
            bdb.create_row(BusinessDb.BRANCHES, "main", {
                "name": business.name, "phone": business.phone, "address": signup.get("address", ""),
                "hours": signup.get("hours") or {},
            }),
            self._checklist.seed(bdb, business.type),
            bdb.set_row(BusinessDb.USERS, business.ownerUid, {
                "role": "owner", "branchIds": [],
                "profile": {"name": owner.displayName, "email": owner.email, "phone": ""},
            }),
            bdb.create_row(BusinessDb.SETTINGS, AGENT_SETTINGS,
                           {"language": tunable("voice.language"), "voice": tunable("voice.voice")}),
            bdb.create_row(BusinessDb.SETTINGS, CONSOLE_SETTINGS,
                           {"language": (business.languages or ["en"])[0]}),
        )
        username = signup.get("username") or owner.username or owner.email.split("@")[0] or owner.uid
        await self._accounts.set_claims(business.ownerUid, {"businessId": business.id, "username": username})
        await self._indexes(business)

    async def _database(self, business: Business) -> None:
        """Its own database, or its region's shared one; nothing when nested in `(default)`."""
        where = place(business.id, business.dbId)
        if where.database and where.root:
            await self._db.create_database(where.database, region_location(business.dbId.partition("/")[0]))
        elif where.database:
            await self._db.create_database(where.database, business.dataLocation or None)

    async def _indexes(self, business: Business) -> None:
        """Requests any index missing (again) and waits until every one is built."""
        name, indexes = _indexed(business)
        await self._db.request_indexes(name, indexes)
        wait, poll = tunable("businesses.indexWaitSeconds"), tunable("businesses.indexPollSeconds")
        await self._db.wait_for_indexes(name, indexes, wait, poll)

    async def _catalogue(self, app_id: str) -> list[str]:
        row = await self._db.get_row(Default.SCOPES, app_id)
        scopes = (row or {}).get("scopes") or tunable("scopes").get(app_id, [])
        return [s["id"] for s in scopes]
