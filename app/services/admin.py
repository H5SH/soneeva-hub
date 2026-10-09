"""App, plan and user management behind the dashboard.

Every method here assumes the caller has already been authorised — scope and app membership
are checked in the route layer, where the path tells us which app is in play.
"""

from typing import Any

from app.models.admin import DEFAULT_ROLES, Principal, Scope, UserRecord
from app.models.app import AppConfig, PlanConfig
from app.models.auth import Application, ApplicationStatus
from app.models.db import Collection
from app.services.db import Database
from app.services.roles import RoleStore
from app.services.tokens.generate import rotate
from app.services.tokens.keys import KeyStore


class AdminError(Exception):
    """A refusal the operator can act on. Rendered as a message, not a stack trace."""


class AdminService:
    def __init__(self, db: Database, keys: KeyStore, roles: RoleStore) -> None:
        self._db = db
        self._keys = keys
        self._roles = roles

    async def _save_app(self, app_id: str, fields: dict[str, Any]) -> None:
        """Write to applications/{id} and drop the verifier's cached copy.

        KeyStore caches that document for 60s, so a key rotated without this is rejected
        until the entry expires. Every write goes through here so a new one cannot forget.
        """
        await self._db.set_row(Collection.APPLICATIONS, app_id, fields)
        self._keys.forget(app_id)

    # ── apps ────────────────────────────────────────────────────────────────
    async def list_apps(self, user: Principal) -> list[AppConfig]:
        apps, _ = await self.list_apps_with_keys(user)
        return apps

    async def list_apps_with_keys(self, user: Principal) -> tuple[list[AppConfig], dict[str, int]]:
        """Apps plus how many keys each holds, from the one query.

        The documents already carry `keys`, so asking for each app again to count them was N
        extra round trips for data in hand.
        """
        rows = await self._db.get_rows(Collection.APPLICATIONS)
        visible = [r for r in rows if user.is_superadmin or r.get("id") in user.apps]
        counts = {r["id"]: len(r.get("keys") or []) for r in visible if r.get("id")}
        return [self._to_config(r) for r in visible], counts

    async def get_app(self, app_id: str) -> AppConfig | None:
        row = await self._db.get_row(Collection.APPLICATIONS, app_id)
        return self._to_config(row) if row else None

    async def get_application(self, app_id: str) -> Application | None:
        """The credentials view of the same document — keys and status."""
        row = await self._db.get_row(Collection.APPLICATIONS, app_id)
        return Application(**(row | {"id": app_id})) if row else None

    async def create_app(
        self,
        app_id: str,
        name: str,
        return_url: str,
        allowed_return_hosts: list[str],
    ) -> AppConfig:
        if not app_id.replace("-", "").replace("_", "").isalnum():
            raise AdminError("Use letters, numbers, hyphens and underscores only.")
        if await self._db.get_row(Collection.APPLICATIONS, app_id):
            raise AdminError(f"An app called '{app_id}' already exists.")

        config = AppConfig(
            id=app_id,
            name=name or app_id,
            return_url=return_url or None,
            allowed_return_hosts=allowed_return_hosts,
            plans=[],
        )
        await self._save_app(
            app_id,
            config.model_dump(mode="json", exclude={"id"})
            | {"status": ApplicationStatus.ACTIVE, "keys": []},
        )
        return config

    async def update_app(self, app_id: str, **fields: Any) -> AppConfig:
        existing = await self.get_app(app_id)
        if not existing:
            raise AdminError("That app no longer exists.")
        await self._save_app(app_id, fields)
        return await self.get_app(app_id)  # type: ignore[return-value]

    async def delete_app(self, app_id: str) -> None:
        """Refuses while anyone is still subscribed.

        Deleting the app would orphan live Stripe subscriptions — they would keep billing with
        nothing left to read their entitlements.
        """
        subscriptions = await self._db.get_rows(
            Collection.SUBSCRIPTIONS, filters={"app_id": app_id}
        )
        active = [s for s in subscriptions if s.get("status") in ("active", "trialing")]
        if active:
            raise AdminError(
                f"{len(active)} store(s) are still subscribed. Cancel those first."
            )
        await self._db.delete_row(Collection.APPLICATIONS, app_id)
        self._keys.forget(app_id)

    async def rotate_key(self, app_id: str) -> tuple[str, str]:
        """Returns (kid, private PEM). The private half is shown once and never stored."""
        application = await self.get_application(app_id)
        if not application:
            raise AdminError("That app no longer exists.")
        application, kid, private_pem = rotate(application)
        await self._save_app(app_id, {"keys": [k.model_dump(mode="json") for k in application.keys]})
        return kid, private_pem

    async def counts(self, user: Principal, apps: list[AppConfig]) -> dict[str, int]:
        """Headline numbers for the dashboard. `apps` is passed in — the caller has it."""
        users = await self.list_users() if user.has(Scope.USER_VIEW) else []
        roles = await self.list_roles() if user.has(Scope.USER_VIEW) else {}
        subscriptions = await self._db.get_rows(Collection.SUBSCRIPTIONS)
        return {
            "apps": len(apps),
            "plans": sum(len(a.plans) for a in apps),
            "users": len(users),
            "unassigned": sum(1 for u in users if not u.role),
            "roles": len(roles),
            "active": sum(
                1 for s in subscriptions if s.get("status") in ("active", "trialing")
            ),
        }

    # ── plans ───────────────────────────────────────────────────────────────
    async def save_plan(self, app_id: str, plan: PlanConfig) -> None:
        config = await self.get_app(app_id)
        if not config:
            raise AdminError("That app no longer exists.")
        plans = [p for p in config.plans if p.code != plan.code]
        plans.append(plan)
        await self._save_app(app_id, {"plans": [p.model_dump(mode="json") for p in plans]})

    async def delete_plan(self, app_id: str, code: str) -> None:
        config = await self.get_app(app_id)
        if not config:
            raise AdminError("That app no longer exists.")
        if config.free_plan_code == code:
            raise AdminError("That is the free plan. Pick a different free plan first.")
        await self._save_app(
            app_id, {"plans": [p.model_dump(mode="json") for p in config.plans if p.code != code]}
        )

    # ── users and roles ─────────────────────────────────────────────────────
    async def list_users(self) -> list[UserRecord]:
        rows = await self._db.get_rows(Collection.USERS)
        return [UserRecord(**row) for row in rows]

    async def save_user(self, uid: str, role: str, apps: list[str], disabled: bool) -> None:
        row = await self._db.get_row(Collection.USERS, uid)
        if not row:
            raise AdminError("No such user.")
        await self._db.set_row(
            Collection.USERS, uid, {"role": role, "apps": apps, "disabled": disabled}
        )

    async def list_roles(self) -> dict[str, list[str]]:
        rows = await self._db.get_rows(Collection.ROLES)
        if rows:
            return {row["id"]: row.get("scopes", []) for row in rows if "id" in row}
        return {name: [str(s) for s in scopes] for name, scopes in DEFAULT_ROLES.items()}

    async def _save_role(self, role: str, scopes: list[str]) -> None:
        """Write a role and drop its cached scopes, so an edit applies on the next request."""
        await self._db.set_row(Collection.ROLES, role, {"scopes": scopes})
        self._roles.forget(role)

    async def save_role(self, role: str, scopes: list[str]) -> None:
        await self._save_role(role, scopes)

    async def role_holders(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for person in await self.list_users():
            if person.role:
                counts[person.role] = counts.get(person.role, 0) + 1
        return counts

    async def create_role(self, role: str, scopes: list[str]) -> str:
        name = role.strip().lower().replace(" ", "-")
        if not name:
            raise AdminError("Give the role a name.")
        if not name.replace("-", "").replace("_", "").isalnum():
            raise AdminError("Use letters, numbers, hyphens and underscores only.")
        if await self._db.get_row(Collection.ROLES, name):
            raise AdminError(f"A role called '{name}' already exists.")
        await self._save_role(name, scopes)
        return name

    async def delete_role(self, role: str) -> None:
        """Refused while anyone holds it — otherwise they keep a role with no scopes behind it."""
        holders = [u for u in await self.list_users() if u.role == role]
        if holders:
            raise AdminError(
                f"{len(holders)} person(s) still have this role. Move them off it first."
            )
        await self._db.delete_row(Collection.ROLES, role)
        self._roles.forget(role)

    @staticmethod
    def _to_config(row: dict[str, Any]) -> AppConfig:
        # The document carries both views — plans here, keys for the token verifier. Pydantic
        # ignores what this model does not declare.
        return AppConfig(**row)
