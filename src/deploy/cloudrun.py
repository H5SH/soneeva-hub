"""Production driver: one Cloud Run service per business, public so Twilio can reach it."""

from google.api_core.exceptions import NotFound
from google.cloud import run_v2
from google.iam.v1 import iam_policy_pb2, policy_pb2

from src.deploy.base import Deployer

#: Service name is `{PREFIX}{businessId}`.
PREFIX = "voice-"


class CloudRunDeployer(Deployer):
    def __init__(self, project: str, region: str, image: str) -> None:
        self._parent = f"projects/{project}/locations/{region}"
        self._image = image
        self._client: run_v2.ServicesAsyncClient | None = None

    @property
    def client(self) -> run_v2.ServicesAsyncClient:
        self._client = self._client or run_v2.ServicesAsyncClient()
        return self._client

    def _id(self, business_id: str) -> str:
        return f"{PREFIX}{business_id}"

    async def deploy(self, business_id: str, env: dict[str, str]) -> str:
        """The URL exists only after the first create, so a new service gets a second revision."""
        name = f"{self._parent}/services/{self._id(business_id)}"
        try:
            known = (await self.client.get_service(name=name)).uri
        except NotFound:
            known = ""
        uri = await self._apply(business_id, name, env | {"PUBLIC_BASE_URL": known}, bool(known))
        if uri != known:
            uri = await self._apply(business_id, name, env | {"PUBLIC_BASE_URL": uri}, True)
        binding = policy_pb2.Binding(role="roles/run.invoker", members=["allUsers"])
        await self.client.set_iam_policy(request=iam_policy_pb2.SetIamPolicyRequest(
            resource=name, policy=policy_pb2.Policy(bindings=[binding])
        ))
        return uri

    async def _apply(self, business_id: str, name: str, env: dict[str, str], exists: bool) -> str:
        container = run_v2.Container(
            image=self._image, env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()]
        )
        service = run_v2.Service(template=run_v2.RevisionTemplate(containers=[container]))
        if exists:
            service.name = name
            operation = await self.client.update_service(service=service)
        else:
            operation = await self.client.create_service(
                parent=self._parent, service=service, service_id=self._id(business_id)
            )
        return (await operation.result()).uri

    async def stop(self, business_id: str) -> None:
        try:
            operation = await self.client.delete_service(
                name=f"{self._parent}/services/{self._id(business_id)}"
            )
            await operation.result()
        except NotFound:
            return
