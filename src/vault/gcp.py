"""Google Secret Manager (roles/secretmanager.admin on the hub's service account)."""

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud.secretmanager_v1 import Replication, Secret, SecretManagerServiceAsyncClient

from src.vault.base import Vault


class SecretManager(Vault):
    def __init__(self, project: str) -> None:
        self._project = project
        self._client: SecretManagerServiceAsyncClient | None = None

    @property
    def client(self) -> SecretManagerServiceAsyncClient:
        self._client = self._client or SecretManagerServiceAsyncClient()
        return self._client

    def _name(self, name: str) -> str:
        return f"projects/{self._project}/secrets/{name}"

    async def read(self, name: str) -> str | None:
        try:
            found = await self.client.access_secret_version(name=f"{self._name(name)}/versions/latest")
        except NotFound:
            return None
        return found.payload.data.decode()

    async def write(self, name: str, value: str) -> None:
        try:
            await self.client.create_secret(
                parent=f"projects/{self._project}", secret_id=name,
                secret=Secret(replication=Replication(automatic=Replication.Automatic())))
        except AlreadyExists:
            pass
        await self.client.add_secret_version(parent=self._name(name), payload={"data": value.encode()})
