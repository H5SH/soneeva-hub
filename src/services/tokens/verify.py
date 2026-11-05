import re

import jwt

from src.models.auth import Audience, Credentials, PublicKey, TokenClaims, Tool
from src.services.tokens.keys import KeyStore
from src.services.tokens.replay import ReplayGuard

ALGORITHMS = ["EdDSA"]
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "jti"]
#: Contract: no caller token may live longer than this.
MAX_LIFETIME_SECONDS = 60
LEEWAY_SECONDS = 30
#: As src/services/tools.py's CALLER_ID, which a token's `iss` is checked against before any database read.
ISSUER = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
#: A business id, checked before it picks a document.
SUBJECT = re.compile(r"^[a-z0-9]{1,64}$")


class AuthError(Exception):
    """Verification failed. The message is safe to return to the caller."""


class TokenVerifier:
    """Verifies a caller's Ed25519 JWT against the public key held for its issuer."""

    def __init__(self, keys: KeyStore, replay: ReplayGuard) -> None:
        self._keys = keys
        self._replay = replay

    async def verify(self, token: str, audience: Audience) -> TokenClaims:
        application, issuer, kid = await self._resolve_issuer(token)
        public_key = application.key(kid) or await self._subject_key(application, token, kid)
        if not public_key:
            raise AuthError("Unknown signing key")

        try:
            payload = jwt.decode(
                token,
                public_key.public_key,
                algorithms=ALGORITHMS,
                audience=audience.value,
                issuer=issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.InvalidTokenError as error:
            raise AuthError(f"Invalid token: {error}") from error

        claims = TokenClaims(**payload)
        if claims.exp - claims.iat > MAX_LIFETIME_SECONDS:
            raise AuthError("Token lifetime is too long")
        if public_key.subject and claims.sub != public_key.subject:
            raise AuthError("Key is not valid for this subject")
        if not self._replay.check_and_record(claims.jti, claims.exp + LEEWAY_SECONDS):
            raise AuthError("Token has already been used")
        return claims

    async def _subject_key(self, application: Credentials, token: str, kid: str | None) -> PublicKey | None:
        """A tool with a key per business: the one on the business the unverified `sub` names. As with `iss`,
        nothing is trusted yet: a forged `sub` picks a key that can't validate the token."""
        if not isinstance(application, Tool) or not application.subject_keys:
            return None
        subject = jwt.decode(token, options={"verify_signature": False}).get("sub")
        if not isinstance(subject, str) or not SUBJECT.match(subject):
            return None
        found = await self._keys.subject_key(subject)
        return found if found and found.kid == kid else None

    async def _resolve_issuer(self, token: str) -> tuple[Credentials, str, str | None]:
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

        if not isinstance(issuer, str) or not ISSUER.match(issuer):
            raise AuthError("Token has no valid issuer")

        application = await self._keys.get(str(issuer))
        if not application:
            raise AuthError("Unknown issuer")
        if not application.enabled:
            raise AuthError("Application is disabled")
        return application, str(issuer), header.get("kid")
