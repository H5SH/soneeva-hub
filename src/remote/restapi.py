import asyncio
import time

import httpx

from src.remote.base import RemoteService


class RestApi(RemoteService):
    async def ready(self, base_url: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                try:
                    if (await client.get(f"{base_url.rstrip('/')}/health")).status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        return False
