"""Businesses, the platform's view of them. Sign-up is src/services/signup.py, where its data goes
src/services/regions.py, provisioning src/services/provision.py."""

from datetime import UTC, datetime
from typing import Any

from src.auth import AccountProvider
from src.auth.password import PasswordSignIn
from src.db import Database, Transaction, business_db
from src.models.admin import SERVICEPERSON
from src.models.db import BusinessDb, Default
from src.models.identity import Identity
from src.models.mode import STAGING_ONLY, Mode
from src.models.platform import Business, BusinessPatch, BusinessPlan, BusinessStatus, PhoneNumber
from src.models.subscription import SubscriptionRecord
from src.services.audit import audit
from src.services.billing import BillingService
from src.services.checklist import ChecklistService
from src.services.errors import PlatformError
from src.services.modes import ByMode
from src.services.people.rules import holds
from src.services.provision import Provisioner
from src.services.regions import DataRegions
from src.services.signup import Signups
from src.services.store_rows import store_record, uninstalled_after
from src.tunables import tunable

#: What a business's own token may change: the owner's sign-off puts it live.
OWNER_FIELDS = {"configured", "status"}
#: What a platform person needs to change a field, other than Businesses.Write.
FIELD_SCOPE = {"servicepersonUid": "Businesses.Assign"}

def _owner_may_set(before: BusinessStatus, status: str | None) -> bool:
    """A business's own token only signs off (pending to running): a hold or delete is the platform's."""
    return status in (None, before) or (before, status) == (BusinessStatus.PENDING, BusinessStatus.RUNNING)

class BusinessService:
    def __init__(self, db: Database, accounts: AccountProvider,
                 billing: ByMode[BillingService],
                 identity: PasswordSignIn | None = None) -> None:
        self._db = db
        self.checklist = ChecklistService(db)
        self._accounts = accounts
        self._billing = billing
        self.provisioner = Provisioner(db, accounts, self.checklist)
        self.regions = DataRegions(db)
        self.signups = Signups(db, accounts, self.regions, identity)

    async def with_plan(self, business: Business, record: SubscriptionRecord | None = None) -> Business:
        """`plan` as the API shows it, {code, status, active}, from its subscription."""
        record = record or await self.record(business)
        plan = None if record.status == "none" else BusinessPlan(
            code=record.plan_code, status=record.status, active=record.is_active)
        return business.model_copy(update={"plan": plan})

    async def record(self, business: Business) -> SubscriptionRecord:
        """Its subscription: what its store said when it pays through one, otherwise Stripe's."""
        billing = self._billing[business.mode]
        return store_record(business) or await billing.get_record(business.appId, business.id)

    def serving(self, business: Business, record: SubscriptionRecord) -> bool:
        """Whether it gets service: on a plan, not deleted, and its store charges aren't long failing."""
        failed = business.chargeFailedAt
        grace = tunable("stores.chargeGraceSeconds")
        overdue = failed and (datetime.now(UTC) - failed).total_seconds() > grace
        return record.is_active and business.status is not BusinessStatus.DELETED and not overdue

    async def get(self, business_id: str) -> Business | None:
        row = await self._db.get_row(Default.BUSINESSES, business_id)
        return Business(**row) if row else None

    async def require(self, caller: Identity | None, business_id: str) -> Business:
        business = await self.get(business_id)
        if business and caller and caller.mode is Mode.TEST and not business.test:
            raise PlatformError(STAGING_ONLY, 403)
        if not business or (caller and not caller.reaches_business(business)):
            raise PlatformError("No such business", 404)
        return business

    async def every(self) -> list[Business]:
        """Sandbox twins left out, as everywhere businesses are listed or counted."""
        rows = await self._db.get_rows(Default.BUSINESSES)
        return [b for b in (Business(**row) for row in rows) if not b.sandboxOf]

    async def visible(
        self, caller: Identity, status: str | None = None, assigned: bool = False
    ) -> list[Business]:
        """`assigned`: only those whose serviceperson is the caller (a person holding Businesses.Access).
        Deleted businesses only when `status` asks for them."""
        filters: dict[str, Any] = {"status": status} if status else {}
        if assigned:
            access = holds(caller.role_scopes or [], "Businesses.Access")
            if not caller.uid or caller.business_id or not access:
                raise PlatformError("assigned=me needs a person holding Businesses.Access", 403)
            filters["servicepersonUid"] = caller.uid
        rows = await self._db.get_rows(Default.BUSINESSES, filters or None)
        found = [b for b in (Business(**r) for r in rows) if caller.reaches_business(b) and not b.sandboxOf
                 and (status or b.status is not BusinessStatus.DELETED)]
        records, none = await self.subscriptions(found), SubscriptionRecord(app_id="", tenant="")
        return [await self.with_plan(b, records.get(b.id, none)) for b in found]

    async def subscriptions(self, found: list[Business]) -> dict[str, SubscriptionRecord]:
        """Each business's subscription by id: its store's, or the newest of one Stripe listing per app."""
        pairs = {(b.mode, b.appId) for b in found}
        listed = {(mode, app): await self._billing[mode].records(app) for mode, app in pairs}
        return {b.id: r for b in found if (r := store_record(b) or listed[b.mode, b.appId].get(b.id))}

    async def branches(self, business: Business) -> list[dict[str, Any]]:
        rows = await self.business_db(business).get_rows(BusinessDb.BRANCHES)
        return [{"id": r["id"], "name": r.get("name", ""), "phone": r.get("phone", ""),
                 "address": r.get("address", "")} for r in rows]

    async def patch(self, caller: Identity, business_id: str, patch: BusinessPatch) -> Business:
        """Pause is status `onhold`; delete is status `deleted` (and restoring from it), which needs
        Businesses.Delete and is never the business's own to make."""
        before = await self.require(caller, business_id)
        fields = patch.model_dump(exclude_unset=True, mode="json")
        deleting = "status" in fields and BusinessStatus.DELETED in (fields["status"], before.status)
        owner_may = set(fields) <= OWNER_FIELDS and _owner_may_set(before.status, fields.get("status"))
        if caller.business_id and not owner_may:
            raise PlatformError("A business may only sign off: configured, and pending to running", 403)
        role = caller.role_scopes
        if role is not None:
            scope_of = FIELD_SCOPE | ({"status": "Businesses.Delete"} if deleting else {})
            need = {scope_of.get(f, "Businesses.Write") for f in fields}
            missing = sorted(n for n in need if not holds(role, n))
            if missing:
                raise PlatformError(f"Requires {', '.join(missing)}", 403)
        uid = fields.get("servicepersonUid")
        if uid:
            person = await self._db.get(Default.USERS, uid) or {}
            if person.get("role") != SERVICEPERSON or person.get("disabled") is True:
                raise PlatformError("No active serviceperson with that uid", 422)
        if fields:
            changes = fields | (uninstalled_after(before, fields["status"]) if "status" in fields else {})
            await self._db.upsert_live(Default.BUSINESSES, business_id, changes)
            action = "business.delete" if fields.get("status") == "deleted" else "business.update"
            await audit(self._db, action, business_id, caller.uid or caller.app_id, fields)
        if "servicepersonUid" in fields:
            await self.checklist.set_state(business_id, "assigned", bool(uid))
        return await self.require(caller, business_id)

    async def delete(self, business_id: str, by: str) -> Business:
        """Status `deleted`, as the dashboard does it; its database and owner account stay."""
        business = await self.get(business_id)
        if not business:
            raise PlatformError("No such business", 404)
        await self.set_fields(business_id, {"status": BusinessStatus.DELETED.value,
                                            **uninstalled_after(business, BusinessStatus.DELETED)})
        await audit(self._db, "business.delete", business_id, by, {"name": business.name})
        return business

    async def add_number(self, business_id: str, number: PhoneNumber) -> None:
        """In a transaction: two purchases at once must both stay."""
        async def run(tx: Transaction) -> None:
            row = await tx.get(Default.BUSINESSES, business_id) or {}
            numbers = [*row.get("numbers", []), number.model_dump()]
            tx.set(Default.BUSINESSES, business_id, {"numbers": numbers})

        await self._db.transaction(run)

    async def set_fields(self, business_id: str, fields: dict[str, Any]) -> None:
        await self._db.upsert_live(Default.BUSINESSES, business_id, fields)

    async def database(self, business_id: str) -> Database | None:
        row = await self._db.get_row(Default.BUSINESSES, business_id)
        return business_db(self._db, business_id, row.get("dbId")) if row else None

    def business_db(self, business: Business) -> Database:
        return business_db(self._db, business.id, business.dbId)
