from pydantic import BaseModel, ConfigDict, Field


class TokenClaims(BaseModel):
    """Payload carried inside the x-api-key JWT (or the ?token= query param)."""

    model_config = ConfigDict(extra="allow")

    store_hash: str
    access_token: str | None = None
    plan: str | None = None
    app_id: str | None = None
    email: str | None = None
    exp: int | None = None
    iat: int | None = None


class AuthContext(BaseModel):
    """Verified caller identity, scoped to the app_id taken from the request path."""

    app_id: str
    store_hash: str
    access_token: str | None = None
    claimed_plan: str | None = Field(
        default=None,
        description="Plan asserted by the caller's token. Informational only, never trusted.",
    )
    email: str | None = None

    @property
    def key(self) -> str:
        return f"{self.app_id}__{self.store_hash}"

    @property
    def gateway_metadata(self) -> dict[str, str]:
        return {"app_id": self.app_id, "store_hash": self.store_hash}
