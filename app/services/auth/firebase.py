import secrets

import firebase_admin
from firebase_admin import auth as firebase_auth

from app.models.admin import SUPERADMIN, Principal, UserRecord
from app.models.db import Collection
from app.services.auth.base import Authenticator, AuthError
from app.services.db import Database
from app.services.roles import RoleStore
from app.services.session import SessionError, issue, read


class FirebaseAuthenticator(Authenticator):
    """Firebase for sign-in; our own signed session afterwards.

    Roles and app membership are read from Firestore on every request, so disabling someone
    or changing their role takes effect immediately rather than when their token expires.
    """

    def __init__(self, db: Database, secret: str, ttl_days: int, roles: RoleStore) -> None:
        self._db = db
        self._secret = secret
        self._ttl_days = ttl_days
        self._roles = roles
        if not firebase_admin._apps:
            firebase_admin.initialize_app()

    async def session_from_login(self, id_token: str, ttl_days: int) -> str:
        if not self._secret:
            raise AuthError("SESSION_SECRET is not set on this service")
        try:
            decoded = firebase_auth.verify_id_token(id_token, check_revoked=True)
        except Exception as error:
            raise AuthError(f"Invalid sign-in: {error}") from error

        user = await self._ensure_user(decoded["uid"], decoded.get("email", ""))
        if user.disabled:
            raise AuthError("This account is disabled")

        return issue(self._secret, user.uid, user.email, ttl_days or self._ttl_days)

    async def principal(self, session_cookie: str) -> Principal:
        if not self._secret:
            raise AuthError("SESSION_SECRET is not set on this service")
        try:
            uid, _ = read(self._secret, session_cookie)
        except SessionError as error:
            raise AuthError(f"Session is not valid: {error}") from error

        row = await self._db.get_row(Collection.USERS, uid)
        if not row:
            raise AuthError("No account for this sign-in")
        user = UserRecord(**row)
        if user.disabled:
            raise AuthError("This account is disabled")

        return Principal(
            uid=user.uid,
            email=user.email,
            role=user.role,
            scopes=await self._roles.scopes(user.role),
            apps=user.apps,
        )

    async def revoke(self, uid: str) -> None:
        """Ends Firebase sign-in everywhere.

        A signed session token cannot be recalled. What makes revocation immediate is
        `disabled` on the user row, which is read on every request.
        """
        firebase_auth.revoke_refresh_tokens(uid)

    async def create_user(self, email: str) -> tuple[str, str]:
        """Create the account, then hand back a link for them to set their own password.

        The random password is never shown — the link is the credential, so no one picks or
        emails a password on someone else's behalf.
        """
        email = email.strip().lower()
        if not email:
            raise AuthError("Enter an email address")
        try:
            user = firebase_auth.create_user(email=email, password=secrets.token_urlsafe(32))
        except firebase_auth.EmailAlreadyExistsError as error:
            raise AuthError(f"{email} already has an account") from error
        except Exception as error:
            raise AuthError(f"Could not create the account: {error}") from error

        record = UserRecord(uid=user.uid, email=email, role="")
        await self._db.set_row(Collection.USERS, user.uid, record.model_dump())

        try:
            link = firebase_auth.generate_password_reset_link(email)
        except Exception:
            # The account exists either way; they can reset from the login page instead.
            link = ""
        return user.uid, link

    async def delete_user(self, uid: str) -> None:
        try:
            firebase_auth.delete_user(uid)
        except firebase_auth.UserNotFoundError:
            pass  # Already gone from Auth; still clear our record below.
        except Exception as error:
            raise AuthError(f"Could not remove the account: {error}") from error
        await self._db.delete_row(Collection.USERS, uid)

    async def _ensure_user(self, uid: str, email: str) -> UserRecord:
        """A first sign-in gets a record; later ones get no role until granted.

        Whoever signs in first had console access to create the account, so they become
        superadmin — that is what makes the dashboard usable without a seed script. Everyone
        after gets a roleless record and sees nothing until granted.
        """
        existing = await self._db.get_row(Collection.USERS, uid)
        record = UserRecord(**existing) if existing else UserRecord(uid=uid, email=email, role="")

        if not record.role and not await self._anyone_has_a_role():
            record.role = SUPERADMIN

        if not existing or record.role:
            await self._db.set_row(Collection.USERS, uid, record.model_dump())
        return record

    async def _anyone_has_a_role(self) -> bool:
        rows = await self._db.get_rows(Collection.USERS)
        return any(row.get("role") for row in rows)

