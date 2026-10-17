from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StoredFile:
    data: bytes
    content_type: str
    etag: str
    updated: datetime


class FileStore(ABC):
    """Files at fixed paths in one private bucket. Paths carry no extension; the type is metadata."""

    @abstractmethod
    async def get(self, path: str) -> StoredFile | None: ...

    @abstractmethod
    async def put(self, path: str, data: bytes, content_type: str) -> datetime:
        """Replaces whatever is there; returns when it was written."""

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Gone already counts as deleted."""
