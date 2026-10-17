"""Apps and their keys behind /v1/payment and the CLI.

Every method here assumes the caller has already been authorised — scope and app membership
are checked in the route layer, where the path tells us which app is in play.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from src.db import Database
from src.models.admin import Principal
from src.models.app import AppConfig
from src.models.auth import Application, ApplicationStatus
from src.models.db import Default, to_doc
from src.models.mode import Mode, staging_id
from src.models.subscription import GatewaySubscription
from src.payment import PaymentGateway
from src.services.errors import AdminError
from src.services.modes import ByMode
from src.services.plans import PlanService
from src.services.tokens.generate import rotate
from src.services.tokens.keys import KeyStore
from src.services.tools import RESERVED, check_new_id
from src.services.webhook import WEBHOOK_EVENTS
from src.utils import slug

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def clean_tags(tags: list[str] | str) -> list[str]:
    """Lowercased, de-duplicated, order kept — so `Acme` from karbon-admin and `acme` from the CLI
    are one tag that one search finds."""
    raw = tags.split(",") if isinstance(tags, str) else tags
    return list(dict.fromkeys(t for t in (slug(tag) for tag in raw) if t))


def matches(app: AppConfig, query: str) -> bool:
    """Id, name or any tag, from one search box."""
    needle = query.strip().lower()
    return not needle or needle in app.id.lower() or needle in app.name.lower() or any(
        needle in tag for tag in app.tags)


class AdminService:
    def __init__(
        self, db: Database, keys: KeyStore, payment: ByMode[PaymentGateway], plans: ByMode[PlanService],
        forget: Callable[[str], None] | None = None,
    ) -> None:
        self._db = db
        self._keys = keys
        self._payment = payment
        self._plans = plans
        self._forget = forget

    async def _save_app(self, app_id: str, fields: dict[str, Any]) -> None:
        """Write to apps/{id} (camelCase) and drop every cached copy: the verifier's keys and
        the AppRegistry's config. Every write goes through here so a new one cannot forget."""
        await self._db.set_row(Default.APPS, app_id, to_doc(fields))
        self._dropped(app_id)

    def _dropped(self, app_id: str) -> None:
        self._keys.forget(app_id)
        if self._forget:
            self._forget(app_id)

    # ── apps ────────────────────────────────────────────────────────────────
    async def list_apps(self, user: Principal, query: str = "") -> list[tuple[AppConfig, Application]]:
        """Apps the person may see and `query` matches (id, name or tag), each with its credentials."""
        rows = await self._db.get_rows(Default.APPS)
        not_apps = RESERVED | {r["id"] for r in await self._db.get_rows(Default.TOOLS)}
        visible = [r for r in rows if r.get("id") and r["id"] not in not_apps
                   and (user.sees_all or r["id"] in user.apps)]
        found = [(AppConfig.of(r["id"], r, await self._plans[Mode.LIVE].for_app(r["id"])), Application(**r))
                 for r in visible]
        return [(config, creds) for config, creds in found if matches(config, query)]

    async def get_app(self, app_id: str, mode: Mode = Mode.LIVE) -> AppConfig | None:
        return await self._plans[mode].app(app_id)

    async def get_application(self, app_id: str) -> Application | None:
        """The credentials view of the same document — keys and status."""
        row = await self._db.get_row(Default.APPS, app_id)
        return Application(**(row | {"id": app_id})) if row else None

    async def create_app(
        self,
        app_id: str,
        name: str,
        return_url: str,
        allowed_return_hosts: list[str],
        tags: list[str] | str | None = None,
    ) -> AppConfig:
        await check_new_id(self._db, app_id)

        config = AppConfig(
            id=app_id,
            name=name or app_id,
            return_url=return_url or None,
            allowed_return_hosts=allowed_return_hosts,
            tags=clean_tags(tags or []),
        )
        await self._save_app(
            app_id,
            config.model_dump(mode="json", exclude={"id", "plans"})
            | {"status": ApplicationStatus.ACTIVE, "keys": []},
        )
        return config

    async def update_app(self, app_id: str, **fields: Any) -> AppConfig:
        existing = await self.get_app(app_id)
        if not existing:
            raise AdminError("That app no longer exists.")
        if "tags" in fields:
            fields["tags"] = clean_tags(fields["tags"])
        await self._save_app(app_id, fields)
        return await self.get_app(app_id)  # type: ignore[return-value]

    async def provision_webhook(self, app_id: str, base_url: str, mode: Mode = Mode.LIVE) -> tuple[str, str]:
        """Create this app's Stripe endpoint in `mode`'s account (test: `/webhooks/stripe/{app}_staging`)
        and store its signing secret, which Stripe returns only at creation. Returns (endpoint id, url);
        the secret is never returned or printed."""
        host = urlparse(base_url).hostname or ""
        if host in LOCAL_HOSTS or host.endswith(".localhost"):
            raise AdminError(
                f"SERVICE_BASE_URL is {base_url!r}; Stripe cannot deliver to a local address. Run "
                "`stripe listen --forward-to localhost:8080/webhooks/stripe/<app>` and put its whsec_ "
                "in STRIPE_WEBHOOK_SECRET instead.")
        if not base_url.startswith("https://"):
            raise AdminError(f"SERVICE_BASE_URL is {base_url!r}; Stripe requires https for a webhook.")
        if not await self.get_app(app_id):
            raise AdminError("That app no longer exists.")
        test = mode is Mode.TEST
        url = f"{base_url.rstrip('/')}/webhooks/stripe/{staging_id(app_id) if test else app_id}"
        endpoint_id, secret = await self._payment[mode].create_webhook_endpoint(url, list(WEBHOOK_EVENTS))
        prefix = "staging" if test else "stripe"
        await self._save_app(
            app_id, {f"{prefix}_webhook_secret": secret, f"{prefix}_webhook_endpoint_id": endpoint_id})
        return endpoint_id, url

    async def subscriptions(self, app_id: str) -> list[tuple[Mode, GatewaySubscription]]:
        """Every subscription of the app, live and test."""
        modes = self._payment.modes()
        return [(m, s) for m in modes for s in await self._payment[m].app_subscriptions(app_id)]

    async def subscribers(self, app_id: str) -> int:
        """Active subscriptions, live and test."""
        return sum(s.is_active for _, s in await self.subscriptions(app_id))

    async def delete_app(self, app_id: str) -> None:
        """Never while anyone is subscribed (their billing would outlive the app); archives its plans."""
        count = await self.subscribers(app_id)
        if count:
            raise AdminError(f"{count} subscriber(s), live or test: an app with subscribers is never deleted")
        for mode in self._plans.modes():
            for plan in await self._plans[mode].for_app(app_id):
                await self._payment[mode].archive_plan(plan)
            self._plans[mode].forget()
        await self._db.delete_row(Default.APPS, app_id)
        self._dropped(app_id)

    async def rotate_key(self, app_id: str) -> tuple[str, str]:
        """Returns (kid, private PEM). The private half is shown once and never stored."""
        application = await self.get_application(app_id)
        if not application:
            raise AdminError("That app no longer exists.")
        keys, kid, private_pem = rotate(application.keys)
        await self._save_app(app_id, {"keys": [k.model_dump(mode="json") for k in keys]})
        return kid, private_pem
