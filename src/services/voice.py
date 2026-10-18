"""One soneeva-voice per business: its env, its deployment, and restarts on settings changes."""

import logging
from datetime import UTC, datetime
from typing import Any

from src.db import Database
from src.deploy import Deployer
from src.models.db import AGENT_SETTINGS, BusinessDb, Default
from src.models.payment import WebhookEvent
from src.models.platform import Business, VoiceSettings
from src.remote import RemoteService
from src.services.businesses import BusinessService
from src.services.tools import ToolService
from src.tunables import tunable

logger = logging.getLogger(__name__)
READY_TIMEOUT_SECONDS = 30.0
#: Checkout of this app deploys a voice; each deployment signs as the tool VOICE_TOOL with its own key.
VOICE_APP = "soneeva"
VOICE_TOOL = "soneeva-voice"
#: Per-business fields on `businesses/{id}` passed as env, left out when empty. Never the master
#: Twilio pair: a voice gets only its business's subaccount, from `secrets/{businessId}`.
BUSINESS_ENV = {"MCP_URL": "mcpUrl", "WHATSAPP_FROM": "whatsappFrom"}


class VoiceService:
    def __init__(self, db: Database, businesses: BusinessService, tools: ToolService,
                 deployer: Deployer, remote: RemoteService, hub_url: str, project: str) -> None:
        self._db = db
        self._businesses = businesses
        self._tools = tools
        self._deployer = deployer
        self._remote = remote
        self._hub_url = hub_url
        self._project = project

    @property
    def deployer(self) -> Deployer:
        return self._deployer

    async def env(self, business: Business) -> dict[str, str]:
        """Issues the business's voice key: a redeploy rotates it, and the business doc names it."""
        bdb = self._businesses.business_db(business)
        agent = await bdb.get_row(BusinessDb.SETTINGS, AGENT_SETTINGS) or {}
        kid, private_pem = await self._tools.issue_subject_key(VOICE_TOOL, business.id)
        await self._businesses.set_fields(business.id, {"voiceKeyId": kid})
        env = {k: getattr(business, f) for k, f in BUSINESS_ENV.items() if getattr(business, f)}
        env |= {
            "BUSINESS_ID": business.id,
            "BUSINESS_NAME": business.name,
            "BUSINESS_TYPE": business.type,
            # soneeva-voice reads it as src/db/place.py does: empty is nested in `(default)`.
            "FIRESTORE_DATABASE": business.dbId,
            "CALL_LANGUAGE": agent.get("language") or tunable("voice.language"),
            "VOICE": agent.get("voice") or tunable("voice.voice"),
            "HUB_URL": self._hub_url,
            "HUB_APP_ID": VOICE_TOOL,
            "HUB_KEY_ID": kid,
            "HUB_PRIVATE_KEY": private_pem.replace("\n", "\\n"),
            # Changes every deploy, so a restart with otherwise equal env still makes a new revision.
            "RESTART_AT": datetime.now(UTC).isoformat(),
        }
        if self._project:
            env["STORAGE_BUCKET"] = f"gs://{self._project}.firebasestorage.app"
        secret = await self._db.get_row(Default.SECRETS, business.id) or {}
        if secret.get("twilioAccountSid"):
            env["TWILIO_ACCOUNT_SID"] = secret["twilioAccountSid"]
            env["TWILIO_AUTH_TOKEN"] = secret["twilioAuthToken"]
        return env

    async def deploy(self, business_id: str) -> str:
        business = await self._businesses.require(None, business_id)
        url = await self._deployer.deploy(business.id, await self.env(business))
        await self._businesses.set_fields(business.id, {"voiceUrl": url, "deployedAt": datetime.now(UTC)})
        ready = await self._remote.ready(url, READY_TIMEOUT_SECONDS)
        await self._businesses.set_fields(business.id, {"voiceReady": ready})
        logger.info("voice for %s at %s (ready=%s)", business.id, url, ready)
        return url

    async def deploy_quietly(self, business_id: str) -> None:
        """For background tasks: a failure is logged and recorded, never raised."""
        try:
            await self.deploy(business_id)
        except Exception as error:  # noqa: BLE001 - recorded on the business for the API
            logger.exception("voice deploy failed for %s", business_id)
            await self._businesses.set_fields(business_id, {"deployError": str(error)[:300]})

    def checkout_business(self, app_id: str, event: WebhookEvent) -> str | None:
        """The business to deploy for, when this event is a completed checkout of the voice app.
        Every endpoint receives every app's events, so the session must name this app."""
        if app_id != VOICE_APP or event.type != "checkout.session.completed":
            return None
        metadata = event.data.get("metadata") or {}
        return metadata.get("tenant") if metadata.get("app") == app_id else None

    async def save_settings(self, business: Business, settings: VoiceSettings) -> dict[str, Any]:
        """Writes settings/agent; a running deployment must restart to pick it up."""
        await self._businesses.business_db(business).set_row(
            BusinessDb.SETTINGS, AGENT_SETTINGS, settings.model_dump()
        )
        return {"saved": True, "restarting": bool(business.voiceUrl)}
