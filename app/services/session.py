"""Session tokens, signed with a secret shared across our applications.

Plain HS256, not NextAuth's JWE cookie: any app holding the secret can verify one in three
lines, with no Firebase admin SDK of its own.
"""

from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"
ISSUER = "payment-service"


class SessionError(Exception):
    """The token is missing, malformed, expired or signed with a different secret."""


def issue(secret: str, uid: str, email: str, ttl_days: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": uid,
            "email": email,
            "iss": ISSUER,
            "iat": now,
            "exp": now + timedelta(days=ttl_days),
        },
        secret,
        algorithm=ALGORITHM,
    )


def read(secret: str, token: str) -> tuple[str, str]:
    """Returns (uid, email). Raises SessionError."""
    try:
        claims = jwt.decode(
            token,
            secret,
            # Pinned: without it a token could claim `alg: none` and verify against nothing.
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["sub", "exp", "iss"]},
        )
    except jwt.InvalidTokenError as error:
        raise SessionError(str(error)) from error
    return claims["sub"], claims.get("email", "")
