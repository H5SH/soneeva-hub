"""Email + password sign-in for /recover: Identity Toolkit checks the password, the Admin SDK the token."""

import asyncio
from typing import Any, cast

import httpx
from firebase_admin import auth

from src.auth.firebase import IDENTITY_TOOLKIT


def _verified_uid(token: str) -> str:
    claims = cast(dict[str, Any], auth.verify_id_token(token, check_revoked=True))
    return str(claims["uid"])


class PasswordSignIn:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def _id_token(self, email: str, password: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{IDENTITY_TOOLKIT}/accounts:signInWithPassword", params={"key": self._api_key},
                json={"email": email, "password": password, "returnSecureToken": True})
        body = cast(dict[str, Any], response.json()) if response.status_code == 200 else {}
        return str(body.get("idToken") or "")

    async def uid(self, email: str, password: str) -> str | None:
        """The account's uid, or None for a wrong password, a disabled account or any failure."""
        try:
            token = await self._id_token(email, password)
            return await asyncio.to_thread(_verified_uid, token) if token else None
        except Exception:
            return None
