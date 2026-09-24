import jwt

from app.auth.keys import KeyStore
from app.auth.replay import ReplayGuard
from app.models.auth import Application, Audience, TokenClaims

ALGORITHMS = ["EdDSA"]
LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "jti"]


class AuthError(Exception):
    """Verification failed. The message is safe to return to the caller."""


class TokenVerifier:
    """Verifies a caller's Ed25519 JWT against the public key held for its issuer."""

    def __init__(self, keys: KeyStore, replay: ReplayGuard) -> None:
        self._keys = keys
        self._replay = replay

    async def verify(self, token: str, audience: Audience) -> TokenClaims:
        application, kid = await self._resolve_issuer(token)
        public_key = application.key(kid)
        if not public_key:
            raise AuthError("Unknown signing key")

        try:
            payload = jwt.decode(
                token,
                public_key.public_key,
                algorithms=ALGORITHMS,
                audience=audience.value,
                issuer=application.id,
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.InvalidTokenError as error:
            raise AuthError(f"Invalid token: {error}") from error

        claims = TokenClaims(**payload)
        if not self._replay.check_and_record(claims.jti, claims.exp):
            raise AuthError("Token has already been used")
        return claims

    async def _resolve_issuer(self, token: str) -> tuple[Application, str | None]:
        """Read iss and kid from the unverified envelope to pick a key, then verify with it.

        Reading before verifying is safe here because nothing is trusted: iss only selects which
        public key to check the signature against, and a forged iss simply picks a key that
        cannot validate the token.
        """
        try:
            header = jwt.get_unverified_header(token)
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        except jwt.PyJWTError as error:
            raise AuthError(f"Malformed token: {error}") from error

        if not issuer:
            raise AuthError("Token has no issuer")

        application = await self._keys.get(str(issuer))
        if not application:
            raise AuthError("Unknown issuer")
        if not application.enabled:
            raise AuthError("Application is disabled")
        return application, header.get("kid")
