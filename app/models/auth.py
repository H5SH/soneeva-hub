from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Audience(StrEnum):
    """Separate audiences keep header tokens and URL tokens from being interchangeable."""

    API = "payment-service"
    HOSTED = "payment-service-hosted"


class KeyStatus(StrEnum):
    ACTIVE = "active"
    RETIRING = "retiring"


class ApplicationStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PublicKey(BaseModel):
    """One Ed25519 public key. Several may be live at once so rotation needs no downtime."""

    kid: str
    alg: str = "EdDSA"
    public_key: str = Field(description="SPKI PEM. The private half never reaches this service.")
    status: KeyStatus = KeyStatus.ACTIVE


class Application(BaseModel):
    """A calling app's credentials. Plans live on the same Firestore document."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    status: ApplicationStatus = ApplicationStatus.ACTIVE
    keys: list[PublicKey] = Field(default_factory=list)
    stripe_webhook_secret: str | None = Field(
        default=None,
        description="Per-app signing secret. Falls back to the global one when unset.",
    )

    @property
    def enabled(self) -> bool:
        return self.status is ApplicationStatus.ACTIVE

    def key(self, kid: str | None) -> PublicKey | None:
        """Match on kid when the token carries one, else the single active key."""
        if kid:
            return next((k for k in self.keys if k.kid == kid), None)
        active = [k for k in self.keys if k.status is KeyStatus.ACTIVE]
        return active[0] if len(active) == 1 else None


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

    @property
    def tenant(self) -> str:
        return self.sub


class AuthContext(BaseModel):
    """Verified caller identity, scoped to the app_id taken from the request path."""

    app_id: str
    tenant: str

    @property
    def store_hash(self) -> str:
        """The persisted spelling of `tenant`.

        `store_hash` is baked into live Firestore ids and Stripe metadata, so renaming it
        would orphan existing customers. Storage keeps the old name; the token carries the
        wider `tenant`, which may be a shop domain or uid.
        """
        return self.tenant

    @property
    def key(self) -> str:
        return tenant_key(self.app_id, self.tenant)

    @property
    def gateway_metadata(self) -> dict[str, str]:
        return {"app_id": self.app_id, "store_hash": self.tenant}


def tenant_key(app_id: str, tenant: str) -> str:
    """The document id for anything scoped to one tenant of one app.

    Single definition on purpose — this was previously rebuilt inline in four places.
    """
    return f"{app_id}__{tenant}"
