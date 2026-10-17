"""Advertisements: the master in `(default)/advertisements/{id}`, a copy in each business it is published to
(`b-{id}/advertisements/{id}`, which the console reads). Every change to the master reaches every copy; a
copy's `dismissed` is the business's own and is never touched here. Deletes are soft, copies too."""

import secrets
import string
from datetime import UTC, datetime
from typing import Any

from src.db import Database
from src.models import paths
from src.models.advertisement import AdFields, AdPatch
from src.models.db import DELETED_FLAG, BusinessDb, Default
from src.models.platform import Caller
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.tunables import tunable

#: Master fields a published copy carries (plus the image paths).
SHOWN = ("title", "body", "buttonLabel", "buttonUrl", "background", "active")
ALPHABET = string.ascii_lowercase + string.digits


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def image_paths(ad_id: str, row: dict[str, Any]) -> dict[str, str | None]:
    return {"iconPath": paths.advertisement_image(ad_id, "icon") if row.get("icon") else None,
            "backgroundPath": paths.advertisement_image(ad_id, "background")
            if row.get("background") == "image" else None}


def view(row: dict[str, Any]) -> dict[str, Any]:
    """The master as /v1/advertisements returns it."""
    ad_id = row["id"]
    fields = AdFields.model_fields
    return ({"id": ad_id} | {k: row.get(k, fields[k].default) for k in fields} | image_paths(ad_id, row)
            | {"businessIds": list(row.get("businessIds") or []), "createdBy": row.get("createdBy", ""),
               "createdAt": _iso(row.get("createdAt")), "updatedAt": _iso(row.get("updatedAt"))})


class AdvertisementService:
    def __init__(self, db: Database, businesses: BusinessService) -> None:
        self._db = db
        self._businesses = businesses

    @staticmethod
    def _background(value: str | None) -> None:
        if value is not None and value not in tunable("advertisements.backgrounds"):
            raise PlatformError(f"Unknown background '{value}'", 422)

    async def every(self) -> list[dict[str, Any]]:
        rows = await self._db.get_rows(Default.ADVERTISEMENTS)
        return sorted((view(r) for r in rows), key=lambda a: str(a["createdAt"] or ""), reverse=True)

    async def require(self, ad_id: str) -> dict[str, Any]:
        row = await self._db.get_row(Default.ADVERTISEMENTS, ad_id)
        if not row:
            raise PlatformError("No such advertisement", 404)
        return row

    async def create(self, body: AdFields, by: str) -> dict[str, Any]:
        self._background(body.background)
        ad_id = "ad-" + "".join(secrets.choice(ALPHABET) for _ in range(10))
        record = body.model_dump() | {"businessIds": [], "createdBy": by}
        await self._db.create_row(Default.ADVERTISEMENTS, ad_id, record)
        return view(await self.require(ad_id))

    async def update(self, ad_id: str, patch: AdPatch) -> dict[str, Any]:
        """Changes the master, then every published copy (their `dismissed` stays as it is)."""
        await self.require(ad_id)
        fields = patch.model_dump(exclude_unset=True, exclude_none=True)
        self._background(fields.get("background"))
        if fields:
            await self._db.set_row(Default.ADVERTISEMENTS, ad_id, fields)
        row = await self.require(ad_id)
        for business_id in row.get("businessIds") or []:
            await self._write_copy(business_id, ad_id, row, fresh=False)
        return view(row)

    async def delete(self, ad_id: str) -> dict[str, Any]:
        row = await self.require(ad_id)
        for business_id in row.get("businessIds") or []:
            await self._withdraw_copy(business_id, ad_id)
        await self._db.delete_row(Default.ADVERTISEMENTS, ad_id)
        return {"deleted": True}

    async def publish(self, caller: Caller, ad_id: str, business_ids: list[str]) -> dict[str, Any]:
        """Only businesses the caller reaches (404 otherwise, before anything is written)."""
        if len(business_ids) > tunable("advertisements.maxBusinesses"):
            raise PlatformError("Too many businesses at once", 422)
        row = await self.require(ad_id)
        wanted = list(dict.fromkeys(business_ids))
        for business_id in wanted:
            await self._businesses.require(caller, business_id)
        had = set(row.get("businessIds") or [])
        for business_id in wanted:
            await self._write_copy(business_id, ad_id, row, fresh=business_id not in had)
        await self._db.set_row(Default.ADVERTISEMENTS, ad_id, {"businessIds": sorted(had | set(wanted))})
        return view(await self.require(ad_id))

    async def withdraw(self, caller: Caller, ad_id: str, business_id: str) -> dict[str, Any]:
        row = await self.require(ad_id)
        await self._businesses.require(caller, business_id)
        await self._withdraw_copy(business_id, ad_id)
        kept = [b for b in row.get("businessIds") or [] if b != business_id]
        await self._db.set_row(Default.ADVERTISEMENTS, ad_id, {"businessIds": kept})
        return view(await self.require(ad_id))

    async def published_to(self, business_id: str, ad_id: str) -> bool:
        """Whether a live copy of the ad is in that business (who may see its images)."""
        bdb = await self._businesses.database(business_id)
        return bool(bdb and await bdb.get_row(BusinessDb.ADVERTISEMENTS, ad_id))

    async def dismissed(self, ad_id: str, business_ids: list[str]) -> int:
        """How many of these businesses dismissed their live copy of the ad."""
        copies = [await bdb.get(BusinessDb.ADVERTISEMENTS, ad_id)
                  for bdb in [await self._businesses.database(b) for b in business_ids] if bdb]
        return sum(bool(c and c.get("dismissed") and not c.get(DELETED_FLAG)) for c in copies)

    async def _write_copy(self, business_id: str, ad_id: str, row: dict[str, Any], fresh: bool) -> None:
        bdb = await self._businesses.database(business_id)
        if bdb is None:
            return
        now = datetime.now(UTC)
        copy = {k: row.get(k, AdFields.model_fields[k].default) for k in SHOWN} | image_paths(ad_id, row)
        # The console reads a colour token or null; an image background is `backgroundPath`.
        copy |= {"background": None if row.get("background") == "image" else row.get("background"),
                 "updatedAt": now, DELETED_FLAG: False}
        existing = await bdb.get(BusinessDb.ADVERTISEMENTS, ad_id)
        if fresh or existing is None or existing.get(DELETED_FLAG):
            copy |= {"publishedAt": now, "dismissed": False, "dismissedAt": None}
        await bdb.set(BusinessDb.ADVERTISEMENTS, ad_id, copy)

    async def _withdraw_copy(self, business_id: str, ad_id: str) -> None:
        bdb = await self._businesses.database(business_id)
        if bdb is not None and await bdb.get(BusinessDb.ADVERTISEMENTS, ad_id) is not None:
            gone = {DELETED_FLAG: True, "updatedAt": datetime.now(UTC)}
            await bdb.set(BusinessDb.ADVERTISEMENTS, ad_id, gone)
