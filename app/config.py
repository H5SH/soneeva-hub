import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.app import AppConfig
from app.utils import get_path

from dotenv import load_dotenv

load_dotenv() # important for loading firebase env

class Settings(BaseSettings):
    """Everything comes from .env / the process environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "production"
    log_level: str = "INFO"
    service_base_url: str = Field(
        default="http://localhost:8080",
        description="Public URL of this service. Used to build Stripe return URLs.",
    )

    codinative_secret: str = Field(description="HMAC secret the apps sign their x-api-key JWT with")
    stripe_secret_key: str
    stripe_webhook_secret: str


class AppRegistry:
    """One JSON file per app under /apps, read on first use and kept in memory."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or get_path("apps")
        self._cache: dict[str, AppConfig] = {}

    def get(self, id: str) -> AppConfig | None:
        if cached := self._cache.get(id):
            return cached
        
        path = self._dir / f"{id}.json"
        if not path.is_file():
            return None
        config = AppConfig(id=id, **json.loads(path.read_text()))
        self._cache[id] = config
        return config

    @property
    def ids(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))


@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore
