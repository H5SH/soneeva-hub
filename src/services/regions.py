"""Where a new business's data goes; src/db/place.py reads its `dbId` back.

A shared region (settings.json `dataRegions`) nests the business: the default one in `(default)`,
another in its `region-{key}` database. A custom location gets a database of its own there, billed
separately. Firestore's locations are listed live, and kept for `locationsCacheSeconds`.
"""

import logging
import time
from typing import Any

from src.db import Database, DatabaseError
from src.services.errors import PlatformError
from src.tunables import tunable

logger = logging.getLogger(__name__)
#: A business's own database is `{OWN_PREFIX}{businessId}`.
OWN_PREFIX = "b-"


def regions() -> dict[str, dict[str, Any]]:
    return tunable("dataRegions.regions")


def default_region(country: str) -> str:
    """The region listing `country`, else the default one."""
    found = [key for key, region in regions().items() if country.upper() in region.get("countries", [])]
    return found[0] if found else tunable("dataRegions.default")


def region_location(key: str) -> str | None:
    """Where region `key`'s shared database is made; None for the default one, in `(default)`."""
    return None if key == tunable("dataRegions.default") else regions()[key]["location"]


class DataRegions:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._locations: list[dict[str, str]] = []
        self._listed_at = 0.0

    async def locations(self) -> list[dict[str, str]]:
        stale = time.monotonic() - self._listed_at > tunable("dataRegions.locationsCacheSeconds")
        if not self._locations or stale:
            self._locations, self._listed_at = await self._db.locations(), time.monotonic()
        return self._locations

    async def offered(self) -> dict[str, Any]:
        """What sign-up offers: the shared regions (with the countries that pick each) and every location."""
        shared = [{"id": key, "label": r["label"], "countries": r.get("countries", [])}
                  for key, r in regions().items()]
        try:
            locations = await self.locations()
        except DatabaseError:  # sign-up still offers the shared regions
            logger.exception("Could not list Firestore locations")
            locations = []
        return {"default": tunable("dataRegions.default"), "regions": shared, "locations": locations}

    async def choose(self, business_id: str, country: str, region: str | None,
                     location: str | None) -> tuple[str, str]:
        """(dbId, dataLocation) for a new business."""
        if location:
            if location not in {loc["id"] for loc in await self.locations()}:
                raise PlatformError(f"Unknown data location '{location}'", 422)
            return f"{OWN_PREFIX}{business_id}", location
        key = region or default_region(country)
        if key not in regions():
            raise PlatformError(f"Unknown data region '{key}'", 422)
        return ("" if region_location(key) is None else f"{key}/{business_id}"), ""
