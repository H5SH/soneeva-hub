from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.db import Database
from src.models.app import AppConfig
from src.models.db import Default
from src.models.mode import Mode
from src.services.modes import ByMode
from src.services.plans import PlanService
from src.services.ttl import MISSING, Ttl

load_dotenv() # important for loading firebase env

class Settings(BaseSettings):
    """Everything comes from .env / the process environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "production"
    log_level: str = "INFO"
    service_base_url: str = Field(
        default="http://localhost:8080",
        description="URL of this service. Voice deployments get it as HUB_URL.",
    )

    stripe_secret_key: str
    stripe_webhook_secret: str = Field(
        default="",
        description="Optional fallback signing secret. Per-app secrets (webhook:create) are preferred, "
        "and the only thing that works once a second app exists.",
    )
    stripe_test_secret_key: str = Field(default="", description="Test mode for `{appId}_staging`; empty: off")
    stripe_test_webhook_secret: str = Field(default="", description="Test mode's fallback signing secret")

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""


    auth_secret: str = Field(default="", description="/recover session cookie; the admin's AUTH_SECRET.")
    firebase_api_key: str = Field(default="", description="/recover password check. Empty: /recover is off.")


CACHE_TTL_SECONDS = 30.0


class AppRegistry:
    """`apps/{id}` briefly cached, with its plans in one mode from that mode's PlanService cache.

    Every app write calls `forget` (AdminService), so an edit applies on the next request; the TTL
    only bounds what another instance of the hub may still hold.
    """

    def __init__(self, db: Database, plans: ByMode[PlanService], ttl: float = CACHE_TTL_SECONDS) -> None:
        self._db = db
        self._plans = plans
        self._rows = Ttl(ttl)

    async def get(self, app_id: str, mode: Mode = Mode.LIVE) -> AppConfig | None:
        row = self._rows.get(app_id)
        if row is MISSING:
            row = self._rows.put(app_id, await self._db.get_row(Default.APPS, app_id))
        return AppConfig.of(app_id, row, await self._plans[mode].for_app(app_id)) if row else None

    def forget(self, app_id: str) -> None:
        self._rows.pop(app_id)


@lru_cache
def get_settings() -> Settings:
    return Settings() # type: ignore
