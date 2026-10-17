from src.remote.base import RemoteService


class FakeRemote(RemoteService):
    async def ready(self, base_url: str, timeout: float) -> bool:
        return True
