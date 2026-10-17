"""Testing driver: `uv run uvicorn` in the soneeva-voice checkout, one port per business."""

import json
import os
import signal
import socket
import subprocess
from pathlib import Path
from typing import Any

from src.deploy.base import Deployer, DeployError
from src.utils import get_path

#: Hub-only secrets a local voice must not inherit from this process.
HUB_ONLY = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
            "AUTH_SECRET", "FIREBASE_API_KEY")


def _inherited() -> dict[str, str]:
    """This process's env minus hub secrets, with the credentials path made absolute."""
    env = {k: v for k, v in os.environ.items() if k not in HUB_ONLY}
    if env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(Path(env["GOOGLE_APPLICATION_CREDENTIALS"]).resolve())
    return env


def _free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


class LocalDeployer(Deployer):
    def __init__(self, repo: str, first_port: int, public_url: str = "",
                 home: Path | None = None) -> None:
        self._repo = get_path(Path(repo).expanduser()).resolve()
        self._first_port = first_port
        self._public_url = public_url
        self._home = home or get_path(".deploy-local")
        self._state = self._home / "state.json"

    def _read(self) -> dict[str, Any]:
        return json.loads(self._state.read_text()) if self._state.exists() else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._home.mkdir(exist_ok=True)
        self._state.write_text(json.dumps(data, indent=2))

    async def stop(self, business_id: str) -> None:
        data = self._read()
        entry = data.pop(business_id, None)
        if entry:
            try:
                os.killpg(entry["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._write(data)

    def _port(self, data: dict[str, Any], business_id: str) -> int:
        if business_id in data:
            return data[business_id]["port"]
        taken = {e["port"] for e in data.values()}
        port = self._first_port
        while port in taken or not _free(port):
            port += 1
        return port

    async def deploy(self, business_id: str, env: dict[str, str]) -> str:
        if not (self._repo / "src" / "main.py").exists():
            raise DeployError(f"deploy.localRepo {self._repo} is not a soneeva-voice checkout")
        await self.stop(business_id)
        data = self._read()
        port = self._port(data, business_id)
        url = self._public_url or f"http://localhost:{port}"
        self._home.mkdir(exist_ok=True)
        log = (self._home / f"{business_id}.log").open("ab")
        process = subprocess.Popen(
            ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", str(port)],
            cwd=self._repo, env=_inherited() | env | {"PORT": str(port), "PUBLIC_BASE_URL": url},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        data[business_id] = {"pid": process.pid, "port": port}
        self._write(data)
        return url
