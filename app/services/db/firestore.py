from datetime import UTC, datetime
from typing import Any

import firebase_admin
from firebase_admin import firestore_async
from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import AsyncClient, FieldFilter, Query

from app.models.db import CREATED_AT, DELETED_AT, DELETED_FLAG, UPDATED_AT, Collection
from app.services.db.base import Database


class FirestoreDatabase(Database):
    """Credentials come from GOOGLE_APPLICATION_CREDENTIALS or the GCP runtime."""

    def __init__(self) -> None:
        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app()
        self._client: AsyncClient = firestore_async.client()

    async def get_rows(
        self,
        collection: Collection,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        order_by: str | None = None,
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        query = self._client.collection(collection.value).where(
            filter=FieldFilter(DELETED_FLAG, "==", False)
        )
        for field, value in (filters or {}).items():
            query = query.where(filter=FieldFilter(field, "==", value))
        if order_by:
            query = query.order_by(
                order_by, direction=Query.DESCENDING if descending else Query.ASCENDING
            )
        if limit:
            query = query.limit(limit)
        return [doc.to_dict() | {"id": doc.id} async for doc in query.stream()]

    async def get_row(self, collection: Collection, row_id: str) -> dict[str, Any] | None:
        snapshot = await self._client.collection(collection.value).document(row_id).get()
        if not snapshot.exists:
            return None
        row = snapshot.to_dict() or {}
        return None if row.get(DELETED_FLAG) else row | {"id": snapshot.id}

    async def create_row(self, collection: Collection, row_id: str, data: dict[str, Any]) -> bool:
        now = datetime.now(UTC)
        try:
            await self._client.collection(collection.value).document(row_id).create(
                data | {DELETED_FLAG: False, DELETED_AT: None, CREATED_AT: now, UPDATED_AT: now}
            )
        except AlreadyExists:
            return False
        return True

    async def set_row(self, collection: Collection, row_id: str, data: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        payload = data | {DELETED_FLAG: False, DELETED_AT: None, UPDATED_AT: now}
        document = self._client.collection(collection.value).document(row_id)
        if not (await document.get()).exists:
            payload[CREATED_AT] = now
        await document.set(payload, merge=True)
        return payload | {"id": row_id}

    async def delete_row(self, collection: Collection, row_id: str) -> bool:
        now = datetime.now(UTC)
        document = self._client.collection(collection.value).document(row_id)
        if not (await document.get()).exists:
            return False
        await document.set({DELETED_FLAG: True, DELETED_AT: now, UPDATED_AT: now}, merge=True)
        return True
