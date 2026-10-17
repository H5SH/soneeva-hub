import asyncio
from datetime import UTC, datetime

import firebase_admin
from firebase_admin import storage
from google.api_core.exceptions import NotFound

from src.cloud import project_id
from src.storage.base import FileStore, StoredFile
from src.tunables import tunable


class FirebaseStorage(FileStore):
    """The Firebase Storage bucket (`media.bucket`), through the Admin SDK. Needs
    roles/storage.objectAdmin on the bucket; clients never reach it (storage.rules)."""

    def __init__(self, bucket: str | None = None) -> None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        self._bucket = storage.bucket(bucket or tunable("media.bucket").format(project=project_id()))

    def _get(self, path: str) -> StoredFile | None:
        blob = self._bucket.get_blob(path)
        if blob is None:
            return None
        data = blob.download_as_bytes()
        return StoredFile(data, blob.content_type or "application/octet-stream", blob.etag or "",
                          blob.updated or datetime.now(UTC))

    def _put(self, path: str, data: bytes, content_type: str) -> datetime:
        blob = self._bucket.blob(path)
        blob.upload_from_string(data, content_type=content_type)
        return blob.updated or datetime.now(UTC)

    def _delete(self, path: str) -> None:
        try:
            self._bucket.blob(path).delete()
        except NotFound:
            pass

    async def get(self, path: str) -> StoredFile | None:
        return await asyncio.to_thread(self._get, path)

    async def put(self, path: str, data: bytes, content_type: str) -> datetime:
        return await asyncio.to_thread(self._put, path, data, content_type)

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete, path)
