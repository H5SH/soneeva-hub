from abc import ABC, abstractmethod


class DeployError(Exception):
    """The voice service could not be started."""


class Deployer(ABC):
    """Runs one soneeva-voice per business."""

    @abstractmethod
    async def deploy(self, business_id: str, env: dict[str, str]) -> str:
        """Create or replace the deployment with this env plus PUBLIC_BASE_URL (its own URL).
        Returns that public base URL."""

    @abstractmethod
    async def stop(self, business_id: str) -> None: ...
