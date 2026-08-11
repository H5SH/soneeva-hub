from abc import ABC, abstractmethod
from typing import Any

from app.models.db import Collection


class Database(ABC):
    """Storage contract. Rows are plain dicts; deletes are always soft."""

    @abstractmethod
    async def get_rows(
        self,
        collection: Collection,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        order_by: str | None = None,
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        """All non-deleted rows matching every filter (field == value)."""

    @abstractmethod
    async def get_row(self, collection: Collection, row_id: str) -> dict[str, Any] | None:
        """One row by id, or None when missing or soft-deleted."""

    @abstractmethod
    async def create_row(self, collection: Collection, row_id: str, data: dict[str, Any]) -> bool:
        """Insert only. False when the id already exists — used for webhook idempotency."""

    @abstractmethod
    async def set_row(self, collection: Collection, row_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Upsert, merging into any existing row. Returns the written payload."""

    @abstractmethod
    async def delete_row(self, collection: Collection, row_id: str) -> bool:
        """Soft delete. False when the row does not exist."""
