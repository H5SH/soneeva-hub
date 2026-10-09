import time
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.app import AppConfig
from app.models.db import Collection
from app.services.db import Database

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

    session_ttl_days: int = 5
    session_secret: str = Field(
        default="",
        description="Shared with every app that accepts this session. Required for /admin.",
    )
    session_cookie_name: str = "codinative_session"
    session_cookie_domain: str = Field(
        default="",
        description="e.g. .example.com to share across subdomains. Blank keeps it to this host.",
    )

    # Public by design: the web config ships to any browser. Admin credentials come from
    # GOOGLE_APPLICATION_CREDENTIALS, which is also where the project id is read from.
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""

    stripe_secret_key: str
    stripe_webhook_secret: str = Field(
        description="Fallback signing secret. Prefer applications/{id}.stripe_webhook_secret."
    )


    @property
    def firebase_web_config(self) -> dict[str, str]:
        return {"apiKey": self.firebase_api_key, "authDomain": self.firebase_auth_domain}


CACHE_TTL_SECONDS = 30.0


class AppRegistry:
    """Apps as stored in Firestore, briefly cached.

    Briefly, not forever: the dashboard edits these, and a permanent cache would serve the old
    plans until the next deploy — which is the redeploy the dashboard exists to remove.
    """

    def __init__(self, db: Database, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._db = db
        self._ttl = ttl
        self._cache: dict[str, tuple[float, AppConfig | None]] = {}

    async def get(self, app_id: str) -> AppConfig | None:
        cached = self._cache.get(app_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        row = await self._db.get_row(Collection.APPLICATIONS, app_id)
        config = AppConfig(**row) if row else None
        self._cache[app_id] = (time.monotonic() + self._ttl, config)
        return config

    async def ids(self) -> list[str]:
        rows = await self._db.get_rows(Collection.APPLICATIONS)
        return sorted(row["id"] for row in rows if "id" in row)


@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore
