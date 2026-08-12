import json
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.app import AppConfig
from app.utils import get_path

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

    def __init__(self) -> None:
        self._dir = get_path("apps")
        self._cache: dict[str, AppConfig] = {}

    def get(self, app_id: str) -> AppConfig | None:
        if cached := self._cache.get(app_id):
            return cached
        
        path = self._dir / f"{app_id}.json"
        print(path, 'pattthhhh')
        if not path.is_file():
            print('not file pattthhhh')
            return None
        
        config = AppConfig(id=app_id, **json.loads(path.read_text()))
        self._cache[app_id] = config
        return config

    @property
    def ids(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))


@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore
