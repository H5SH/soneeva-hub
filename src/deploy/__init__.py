import os

from src.cloud import project_id, region
from src.config import Settings
from src.deploy.base import Deployer, DeployError
from src.deploy.cloudrun import CloudRunDeployer
from src.deploy.fake import FakeDeployer
from src.deploy.local import LocalDeployer
from src.tunables import tunable


def Deploy(settings: Settings) -> Deployer:  # noqa: N802 - reads like the other resource exports
    """Cloud Run when this hub runs on Cloud Run (it sets K_SERVICE), otherwise local processes."""
    if os.environ.get("K_SERVICE"):
        image = tunable("deploy.image").format(region=region(), project=project_id())
        return CloudRunDeployer(project_id(), region(), image)
    return LocalDeployer(tunable("deploy.localRepo"), tunable("deploy.localPortStart"))


__all__ = ["CloudRunDeployer", "Deploy", "DeployError", "Deployer", "FakeDeployer", "LocalDeployer"]
