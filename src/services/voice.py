"""Each business's soneeva-voice env, kept in Secret Manager as `voice-{businessId}` (KEY="value" lines).

Written once when the business subscribes (its own voice key: only the public half stays here); its
Twilio subaccount is added when its first number is bought. A developer deploys the voice by hand,
mounting that secret, and its URL is saved here (Voice.Url.Write), which points the numbers at it.
Nothing here deploys or restarts a voice: it reads its settings, menus and recordings from the
business's data and follows their changes itself.
"""

import io
import logging

from dotenv import dotenv_values

from src.db import Database
from src.models.db import Default
from src.models.payment import WebhookEvent
from src.models.platform import Business
from src.numbers import NumberProvider, NumbersError
from src.remote import RemoteService
from src.services.businesses import BusinessService, audit
from src.services.errors import PlatformError
from src.services.tools import ToolService
from src.vault import Vault

logger = logging.getLogger(__name__)
READY_TIMEOUT_SECONDS = 30.0
#: Subscribing to this app gets a voice env; each voice signs as the tool VOICE_TOOL with its own key.
VOICE_APP = "soneeva"
VOICE_TOOL = "soneeva-voice"
#: Paths on the voice service that a number's calls and status callbacks go to.
WEBHOOK_PATH = "/voice"
STATUS_PATH = "/voice/status"


def secret_name(business_id: str) -> str:
    return f"voice-{business_id}"


def to_dotenv(env: dict[str, str]) -> str:
    quoted = {k: v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") for k, v in env.items()}
    return "".join(f'{k}="{v}"\n' for k, v in quoted.items())


def from_dotenv(text: str) -> dict[str, str]:
    return {k: v for k, v in dotenv_values(stream=io.StringIO(text)).items() if v is not None}


class VoiceService:
    def __init__(self, db: Database, businesses: BusinessService, tools: ToolService, vault: Vault,
                 numbers: NumberProvider, remote: RemoteService, hub_url: str, project: str) -> None:
        self._db = db
        self._businesses = businesses
        self._tools = tools
        self._vault = vault
        self._numbers = numbers
        self._remote = remote
        self._hub_url = hub_url
        self._project = project

    async def issue_env(self, business: Business) -> dict[str, str]:
        """A new voice key for the business (the previous one stops working at once) and its env."""
        kid, private_pem = await self._tools.issue_subject_key(VOICE_TOOL, business.id)
        await self._businesses.set_fields(business.id, {"voiceKeyId": kid})
        env = {
            "BUSINESS_ID": business.id,
            "BUSINESS_NAME": business.name,
            "BUSINESS_TYPE": business.type,
            # soneeva-voice reads it as src/db/place.py does: empty is nested in `(default)`.
            "FIRESTORE_DATABASE": business.dbId,
            "HUB_URL": self._hub_url,
            "HUB_APP_ID": VOICE_TOOL,
            "HUB_KEY_ID": kid,
            "HUB_PRIVATE_KEY": private_pem,
        }
        if self._project:
            env["STORAGE_BUCKET"] = f"gs://{self._project}.firebasestorage.app"
        secret = await self._db.get_row(Default.SECRETS, business.id) or {}
        if secret.get("twilioAccountSid"):
            env["TWILIO_ACCOUNT_SID"] = secret["twilioAccountSid"]
            env["TWILIO_AUTH_TOKEN"] = secret["twilioAuthToken"]
        return env

    async def provide(self, business_id: str) -> None:
        """The business's secret, once: a repeated subscription event leaves it (and its key) alone."""
        if await self._vault.read(secret_name(business_id)) is not None:
            return
        business = await self._businesses.require(None, business_id)
        await self._vault.write(secret_name(business_id), to_dotenv(await self.issue_env(business)))

    async def provide_quietly(self, business_id: str) -> None:
        """For background tasks: a failure is logged, never raised."""
        try:
            await self.provide(business_id)
        except Exception:  # noqa: BLE001 - the webhook has answered already
            logger.exception("voice secret failed for %s", business_id)

    async def add(self, business_id: str, fields: dict[str, str]) -> None:
        """Merged into the business's secret as a new version; the next voice revision gets it."""
        await self.provide(business_id)
        env = from_dotenv(await self._vault.read(secret_name(business_id)) or "")
        await self._vault.write(secret_name(business_id), to_dotenv(env | fields))

    def subscribed_business(self, app_id: str, event: WebhookEvent) -> str | None:
        """The business whose voice env is due, when this event is a completed checkout of the voice
        app. Every endpoint receives every app's events, so the session must name this app."""
        if app_id != VOICE_APP or event.type != "checkout.session.completed":
            return None
        metadata = event.data.get("metadata") or {}
        return metadata.get("tenant") if metadata.get("app") == app_id else None

    async def set_url(self, business: Business, url: str, by: str) -> Business:
        """Where the developer deployed it: it must answer /health, and every number follows it."""
        url = url.rstrip("/")
        if not await self._remote.ready(url, READY_TIMEOUT_SECONDS):
            raise PlatformError(f"{url}/health does not answer", 422)
        try:
            for number in business.numbers:
                if number.sid and business.twilioAccountSid:
                    await self._numbers.point(business.twilioAccountSid, number.sid,
                                              url + WEBHOOK_PATH, url + STATUS_PATH)
        except NumbersError as error:
            raise PlatformError(str(error), 502) from error
        await self._businesses.set_fields(business.id, {"voiceUrl": url})
        await audit(self._db, "voice.url", business.id, by, {"voiceUrl": url})
        return business.model_copy(update={"voiceUrl": url})
