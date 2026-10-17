from abc import ABC, abstractmethod


class RemoteService(ABC):
    """Unsigned reachability checks on other platform services. karbon-hub never calls them with
    credentials: it only receives calls."""

    @abstractmethod
    async def ready(self, base_url: str, timeout: float) -> bool:
        """Whether the service answers its health check within `timeout` seconds."""
