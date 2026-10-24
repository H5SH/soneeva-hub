"""A business's sandbox twin: a test business of its own (Stripe test mode, its own data nested in
`(default)`, no numbers), never listed, that a developer runs soneeva-voice against locally.

Only a platform person holding Voice.Sandbox who reaches the live business opens it or rotates its
voice key; the key is shown once, as a local `.env`, and rotating stops the previous one at once.
"""

from datetime import UTC, datetime
from typing import Any

from src.db import Database
from src.models.db import Default
from src.models.platform import Business, BusinessStatus, Caller
from src.services.businesses import BusinessService, audit, new_business_id
from src.services.errors import PlatformError
from src.services.people.rules import holds
from src.services.voice import VoiceService, to_dotenv

SCOPE = "Voice.Sandbox"


class SandboxService:
    def __init__(self, db: Database, businesses: BusinessService, voice: VoiceService) -> None:
        self._db = db
        self._businesses = businesses
        self._voice = voice

    def _check(self, caller: Caller, business: Business) -> None:
        if caller.business_id or not holds(caller.person_scopes or [], SCOPE):
            raise PlatformError(f"Requires {SCOPE}", 403)
        if business.sandboxOf:
            raise PlatformError("This is a sandbox already", 409)

    async def twin(self, caller: Caller, business: Business) -> Business:
        """Made and provisioned on first use; the live business names it as `sandboxId`."""
        self._check(caller, business)
        found = await self._businesses.get(business.sandboxId) if business.sandboxId else None
        if found:
            return found
        twin = Business(
            id=new_business_id(), name=f"{business.name} (sandbox)", type=business.type,
            appId=business.appId, dbId="", ownerUid=business.ownerUid, status=BusinessStatus.RUNNING,
            test=True, country=business.country, timezone=business.timezone, languages=business.languages,
            phone=business.phone, email=business.email, sandboxOf=business.id, createdAt=datetime.now(UTC),
        )
        await self._db.set_row(Default.BUSINESSES, twin.id,
                               twin.model_dump(mode="json", exclude={"id", "plan"}) | {"signup": {}})
        await self._businesses.set_fields(business.id, {"sandboxId": twin.id})
        await audit(self._db, "sandbox.create", business.id, caller.uid or caller.app_id, {"twin": twin.id})
        return await self._businesses.provisioner.run(twin.id)

    async def rotate_key(self, caller: Caller, business: Business) -> dict[str, Any]:
        """The twin's whole local `.env`, with a new key: shown this once, never stored."""
        twin = await self.twin(caller, business)
        env = await self._voice.issue_env(twin)
        await audit(self._db, "sandbox.key", business.id, caller.uid or caller.app_id, {"twin": twin.id})
        return {"businessId": twin.id, "keyId": env["HUB_KEY_ID"], "env": to_dotenv(env)}
