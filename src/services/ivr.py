"""Per-number call menus and recorded messages, in the business database and at fixed storage paths.

    numbers/{e164}                          NumberConfig + updatedAt, updatedBy
    audios/{name}                           {text, language, version, status, reason, updatedAt, versions}
    businesses/{id}/audios/{name}/active    what callers hear (written by the voice service)
    businesses/{id}/audios/{name}/v{n}      an earlier active, kept until the business deletes it
                                            (paths: src/models/paths.py)

The voice service reads all of it at startup, so every change here is followed by a restart.
"""

from datetime import UTC, datetime
from typing import Any

from src.db import Database
from src.models import paths
from src.models.db import CREATED_AT, DELETED_AT, DELETED_FLAG, UPDATED_AT, BusinessDb
from src.models.ivr import AudioPut, NumberConfig
from src.models.platform import Business
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.storage import FileStore

#: The recording a newly bought number greets with, when the business has made one.
DEFAULT_GREETING = "opening"
#: Fields of a stored config that are bookkeeping, not configuration.
STAMPS = ("id", "updatedBy", DELETED_FLAG, DELETED_AT, CREATED_AT, UPDATED_AT)


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def _config(row: dict[str, Any] | None) -> NumberConfig:
    """A stored config, or the default for a number nobody has set up. Unreadable counts as unset."""
    try:
        return NumberConfig(**{k: v for k, v in (row or {}).items() if k not in STAMPS})
    except ValueError:
        return NumberConfig()


def _audio(row: dict[str, Any], used_by: list[str]) -> dict[str, Any]:
    versions = [{"n": int(v.get("n", 0)), "text": str(v.get("text", "")), "at": _iso(v.get("at"))}
                for v in row.get("versions") or [] if isinstance(v, dict)]
    return {
        "name": row["id"], "text": row.get("text", ""), "language": row.get("language", ""),
        "version": int(row.get("version") or 1), "status": row.get("status", "pending"),
        "reason": row.get("reason", ""), "updatedAt": _iso(row.get("updatedAt")),
        "versions": sorted(versions, key=lambda v: v["n"]), "usedBy": used_by,
    }


class IvrService:
    def __init__(self, businesses: BusinessService, files: FileStore) -> None:
        self._businesses = businesses
        self._files = files

    def _db(self, business: Business) -> Database:
        return self._businesses.business_db(business)

    @staticmethod
    def _own(business: Business) -> list[str]:
        return [n.phoneNumber for n in business.numbers]

    async def _configs(self, business: Business) -> dict[str, tuple[bool, NumberConfig]]:
        db = self._db(business)
        out = {}
        for phone in self._own(business):
            row = await db.get_row(BusinessDb.NUMBERS, phone)
            out[phone] = (row is not None, _config(row))
        return out

    # Numbers

    async def numbers(self, business: Business) -> list[dict[str, Any]]:
        return [{"phoneNumber": phone, "configured": configured, "config": config.model_dump()}
                for phone, (configured, config) in (await self._configs(business)).items()]

    async def save_number(
        self, business: Business, phone: str, config: NumberConfig, by: str
    ) -> dict[str, Any]:
        if phone not in self._own(business):
            raise PlatformError("Not one of this business's numbers", 404)
        db = self._db(business)
        missing = sorted([n for n in config.audios() if not await db.get_row(BusinessDb.AUDIOS, n)])
        if missing:
            raise PlatformError(f"No recording named {', '.join(missing)}", 422)
        for option in config.options:
            if option.branchId and not await db.get_row(BusinessDb.BRANCHES, option.branchId):
                raise PlatformError(f"Key {option.digit}: no branch '{option.branchId}'", 422)
            if option.human and option.human.phone in self._own(business):
                raise PlatformError(f"Key {option.digit} would ring this business's own line", 422)
        await db.set_row(BusinessDb.NUMBERS, phone, config.model_dump()
                         | {"updatedAt": datetime.now(UTC), "updatedBy": by})
        return {"phoneNumber": phone, "configured": True, "config": config.model_dump()}

    async def default_number(self, business: Business, phone: str) -> None:
        """A newly bought number: straight to the agent, greeted by `opening` when it exists."""
        db = self._db(business)
        if await db.get_row(BusinessDb.NUMBERS, phone):
            return
        greeting = DEFAULT_GREETING if await db.get_row(BusinessDb.AUDIOS, DEFAULT_GREETING) else ""
        await db.set_row(BusinessDb.NUMBERS, phone, NumberConfig(greeting=greeting).model_dump()
                         | {"updatedAt": datetime.now(UTC), "updatedBy": "number.buy"})

    # Recordings

    async def _used_by(self, business: Business) -> dict[str, list[str]]:
        used: dict[str, list[str]] = {}
        for phone, (configured, config) in (await self._configs(business)).items():
            for name in config.audios() if configured else ():
                used.setdefault(name, []).append(phone)
        return used

    async def audios(self, business: Business) -> list[dict[str, Any]]:
        used = await self._used_by(business)
        rows = await self._db(business).get_rows(BusinessDb.AUDIOS)
        return [_audio(r, used.get(r["id"], [])) for r in sorted(rows, key=lambda r: r["id"])]

    async def _row(self, business: Business, name: str) -> dict[str, Any]:
        row = await self._db(business).get_row(BusinessDb.AUDIOS, name)
        if not row:
            raise PlatformError("No such recording", 404)
        return row

    async def audio(self, business: Business, name: str) -> dict[str, Any]:
        row = await self._row(business, name)
        return _audio(row, (await self._used_by(business)).get(name, []))

    async def save_audio(
        self, business: Business, name: str, body: AudioPut, by: str
    ) -> tuple[dict[str, Any], bool]:
        """Returns (the recording, whether anything changed). The active file moves to v{n} first."""
        db = self._db(business)
        old = await db.get_row(BusinessDb.AUDIOS, name)
        if old and old.get("text") == body.text and old.get("language", "") == body.language:
            return _audio(old, (await self._used_by(business)).get(name, [])), False
        versions = list((old or {}).get("versions") or [])
        active = await self._files.get(paths.audio(business.id, name)) if old else None
        if old and active:
            n = max([int(v.get("n", 0)) for v in versions] + [0]) + 1
            await self._files.put(paths.audio(business.id, name, n), active.data, active.content_type)
            at = old.get("updatedAt") or datetime.now(UTC)
            versions.append({"n": n, "text": old.get("text", ""), "at": at})
        row = {"text": body.text, "language": body.language, "status": "pending", "reason": "",
               "version": int((old or {}).get("version") or 0) + 1, "versions": versions,
               "updatedAt": datetime.now(UTC), "updatedBy": by}
        await db.set_row(BusinessDb.AUDIOS, name, row)
        return await self.audio(business, name), True

    async def delete_version(self, business: Business, name: str, n: int) -> None:
        row = await self._row(business, name)
        versions = [v for v in row.get("versions") or [] if int(v.get("n", 0)) != n]
        if len(versions) == len(row.get("versions") or []):
            raise PlatformError("No such version", 404)
        await self._files.delete(paths.audio(business.id, name, n))
        await self._db(business).set_row(BusinessDb.AUDIOS, name, {"versions": versions})

    async def delete_audio(self, business: Business, name: str) -> None:
        """The recording with every version. Refused while a number plays it."""
        row = await self._row(business, name)
        used = (await self._used_by(business)).get(name)
        if used:
            raise PlatformError(f"Used by {', '.join(used)}: change those numbers first", 409)
        for version in row.get("versions") or []:
            await self._files.delete(paths.audio(business.id, name, int(version.get("n", 0))))
        await self._files.delete(paths.audio(business.id, name))
        await self._db(business).delete_row(BusinessDb.AUDIOS, name)
