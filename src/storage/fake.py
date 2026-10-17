import hashlib
from datetime import UTC, datetime

from src.storage.base import FileStore, StoredFile


class FakeStorage(FileStore):
    """In memory, for tests."""

    def __init__(self) -> None:
        self.files: dict[str, StoredFile] = {}

    async def get(self, path: str) -> StoredFile | None:
        return self.files.get(path)

    async def put(self, path: str, data: bytes, content_type: str) -> datetime:
        now = datetime.now(UTC)
        etag = hashlib.md5(data + now.isoformat().encode()).hexdigest()
        self.files[path] = StoredFile(data, content_type, etag, now)
        return now

    async def delete(self, path: str) -> None:
        self.files.pop(path, None)
