from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.models.db import STORED
from src.models.mode import Mode


class Audience(StrEnum):
    """`payment-service` for /v1/apps/{app_id} (x-api-key), `karbon-hub` for the rest of /v1."""

    API = "payment-service"
    HUB = "karbon-hub"


class KeyStatus(StrEnum):
    ACTIVE = "active"
    RETIRING = "retiring"


class ApplicationStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PublicKey(BaseModel):
    """One Ed25519 public key. Several may be live at once so rotation needs no downtime."""

    model_config = STORED

    kid: str
    alg: str = "EdDSA"
    public_key: str = Field(description="SPKI PEM. The private half never reaches this service.")
    status: KeyStatus = KeyStatus.ACTIVE
    subject: str | None = Field(default=None, description="When set, tokens must carry this sub.")


class Credentials(BaseModel):
    """A caller's keys and hub scopes: an app (`apps/{id}`) or an internal tool (`tools/{id}`)."""

    model_config = ConfigDict(**STORED, extra="ignore")

    id: str
    name: str = ""
    status: ApplicationStatus = ApplicationStatus.ACTIVE
    keys: list[PublicKey] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list, description="hub.* scopes this caller may use.")

    @property
    def enabled(self) -> bool:
        return self.status is ApplicationStatus.ACTIVE

    def key(self, kid: str | None) -> PublicKey | None:
        """Match on kid when the token carries one, else the single active key."""
        if kid:
            return next((k for k in self.keys if k.kid == kid), None)
        active = [k for k in self.keys if k.status is KeyStatus.ACTIVE]
        return active[0] if len(active) == 1 else None


class Application(Credentials):
    """An app's credentials, on the same `apps/{appId}` document as its AppConfig."""

    stripe_webhook_secret: str | None = Field(
        default=None, description="Per-app signing secret. Falls back to the global one when unset.")
    stripe_webhook_endpoint_id: str | None = None
    staging_webhook_secret: str | None = Field(default=None, description="The same, in Stripe test mode.")
    staging_webhook_endpoint_id: str | None = None

    def webhook_secret(self, mode: Mode) -> str | None:
        return self.staging_webhook_secret if mode is Mode.TEST else self.stripe_webhook_secret


class Tool(Credentials):
    """An internal tool: hub keys, never a Stripe link, no staging twin."""

    subject_keys: bool = Field(default=False, description="One key per business, issued at each deploy.")


class TokenClaims(BaseModel):
    """Verified payload of the caller's Ed25519 JWT.

    Identity only. Descriptive data (customer email, display name) travels in the request body:
    putting the tenant id anywhere but the signature would let a buggy caller act on any tenant.
    """

    model_config = ConfigDict(extra="ignore")

    iss: str = Field(description="application_id — selects the public key")
    sub: str = Field(description="Tenant id: store hash, shop domain or firebase uid")
    aud: Audience
    jti: str
    iat: int
    exp: int
    uid: str | None = Field(default=None, description="The person acting, when sub is '-'.")

    @property
    def tenant(self) -> str:
        return self.sub


class AuthContext(BaseModel):
    """Verified caller identity, scoped to the app_id taken from the request path."""

    app_id: str
    tenant: str

    @property
    def gateway_metadata(self) -> dict[str, str]:
        """On the Stripe customer, checkout session and subscription."""
        return {"app": self.app_id, "tenant": self.tenant}
