"""The admin dashboard's Auth.js (next-auth v5) session cookie, read with the shared AUTH_SECRET.

As @auth/core/jwt.js: a JWE (`dir`, `A256CBC-HS512`) whose 64-byte key is HKDF-SHA256 of the secret,
salted with the cookie name; a cookie over ~4KB is split into `<name>.0`, `<name>.1`, …
"""

import json
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Request
from joserfc import jwe
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

NAMES = ("__Secure-authjs.session-token", "authjs.session-token")
ALGORITHMS = ["dir", "A256CBC-HS512"]


def derive_key(secret: str, salt: str) -> bytes:
    info = f"Auth.js Generated Encryption Key ({salt})".encode()
    return HKDF(hashes.SHA256(), 64, salt.encode(), info).derive(secret.encode())


def _cookie(cookies: dict[str, str], name: str) -> str:
    if name in cookies:
        return cookies[name]
    chunks: list[str] = []
    i = 0
    while f"{name}.{i}" in cookies:
        chunks.append(cookies[f"{name}.{i}"])
        i += 1
    return "".join(chunks)


def _uid(value: str, secret: str, salt: str) -> str | None:
    try:
        key = OctKey.import_key(derive_key(secret, salt))
        claims: dict[str, Any] = json.loads(jwe.decrypt_compact(value, key, ALGORITHMS).plaintext or b"")
        exp, uid = claims.get("exp"), claims.get("uid")
    except (JoseError, ValueError, AttributeError):  # forged, wrong secret, or not a JSON object
        return None
    live = isinstance(exp, int | float) and exp > time.time()
    return uid if live and isinstance(uid, str) and uid else None


def admin_session(request: Request) -> str | None:
    """The signed-in person's uid (the admin's `uid` claim), or None when absent, forged or expired."""
    settings = request.app.state.settings
    secret = settings.auth_secret if settings else ""
    for name in NAMES:
        value = _cookie(request.cookies, name)
        if secret and value and (uid := _uid(value, secret, name)):
            return uid
    return None
