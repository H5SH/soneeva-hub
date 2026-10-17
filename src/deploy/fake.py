from src.deploy.base import Deployer


class FakeDeployer(Deployer):
    """In memory, for tests: records each deployment."""

    def __init__(self) -> None:
        self.deployed: list[tuple[str, dict[str, str]]] = []

    async def deploy(self, business_id: str, env: dict[str, str]) -> str:
        self.deployed.append((business_id, env))
        return f"https://voice-{business_id}.example"

    async def stop(self, business_id: str) -> None: ...
