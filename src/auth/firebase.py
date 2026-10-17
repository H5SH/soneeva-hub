import asyncio
from datetime import UTC, datetime
from typing import Any

import firebase_admin
import google.auth
import httpx
from firebase_admin import auth
from google.auth.transport.requests import Request

from src.auth.base import AccountError, AccountProvider
from src.cloud import project_id
from src.models.people import Account

IDENTITY_TOOLKIT = "https://identitytoolkit.googleapis.com/v1"
PAGE_SIZE = 1000
OAUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _iso(millis: int | None) -> str | None:
    return datetime.fromtimestamp(millis / 1000, UTC).isoformat() if millis else None


def to_account(record: auth.UserRecord) -> Account:
    claims = record.custom_claims or {}
    username, business = claims.get("username"), claims.get("businessId")
    return Account(
        uid=record.uid, email=record.email or "", displayName=record.display_name or "",
        disabled=record.disabled, username=username if isinstance(username, str) else None,
        businessId=business if isinstance(business, str) and business else None,
        createdAt=_iso(record.user_metadata.creation_timestamp),
        lastSignInAt=_iso(record.user_metadata.last_sign_in_timestamp),
    )


def _translate(error: Exception) -> AccountError:
    if isinstance(error, auth.EmailAlreadyExistsError):
        return AccountError("email-exists", "That email already has an account")
    if isinstance(error, auth.UserNotFoundError):
        return AccountError("not-found", "No such account")
    if isinstance(error, ValueError):
        return AccountError("invalid-input", str(error))
    return AccountError("unavailable", f"Account call failed: {error}")


def _access_token() -> str:
    credentials, _ = google.auth.default(scopes=[OAUTH_SCOPE])
    credentials.refresh(Request())
    return credentials.token


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as error:
        raise _translate(error) from error


class FirebaseAccounts(AccountProvider):
    """Firebase Auth through the Admin SDK (needs roles/firebaseauth.admin)."""

    def __init__(self) -> None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()

    async def page(self, token: str | None) -> tuple[list[Account], str | None]:
        result = await _call(auth.list_users, page_token=token, max_results=PAGE_SIZE)
        return [to_account(u) for u in result.users], result.next_page_token or None

    async def get(self, uid: str) -> Account | None:
        try:
            return to_account(await _call(auth.get_user, uid))
        except AccountError as error:
            if error.code == "not-found":
                return None
            raise

    async def create(self, email: str, password: str, display_name: str, claims: dict[str, Any]) -> Account:
        record = await _call(auth.create_user, email=email, password=password,
                             display_name=display_name, email_verified=False)
        try:
            await _call(auth.set_custom_user_claims, record.uid, claims)
        except AccountError:
            await self.delete(record.uid)
            raise
        return (await self.get(record.uid)) or to_account(record)

    async def update(self, uid: str, fields: dict[str, Any]) -> Account:
        names = {"email": "email", "password": "password", "displayName": "display_name",
                 "disabled": "disabled"}
        changes = {names[k]: v for k, v in fields.items() if k in names and v is not None}
        record = await _call(auth.update_user, uid, **changes) if changes else await _call(auth.get_user, uid)
        return to_account(record)

    async def set_claims(self, uid: str, claims: dict[str, Any]) -> None:
        current = dict((await _call(auth.get_user, uid)).custom_claims or {})
        merged = {k: v for k, v in (current | claims).items() if v is not None}
        await _call(auth.set_custom_user_claims, uid, merged)

    async def delete(self, uid: str) -> None:
        try:
            await _call(auth.delete_user, uid)
        except AccountError as error:
            if error.code != "not-found":
                raise

    async def reset_link(self, email: str) -> str:
        return await _call(auth.generate_password_reset_link, email)

    async def send_reset(self, email: str) -> None:
        """Identity Toolkit with the service account's own token: no web API key needed."""
        token = await _call(_access_token)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{IDENTITY_TOOLKIT}/projects/{project_id()}/accounts:sendOobCode",
                                         headers={"Authorization": f"Bearer {token}"},
                                         json={"requestType": "PASSWORD_RESET", "email": email})
        if response.status_code != 200:
            raise AccountError("unavailable", "Could not send the reset email")
