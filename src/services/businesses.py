"""Businesses: sign-up (provisioning of `b-{id}` is src/services/provision.py), and the platform's view."""

import secrets
import string
import uuid
from datetime import UTC, datetime
from typing import Any

from src.auth import AccountError, AccountProvider
from src.auth.password import PasswordSignIn
from src.db import Database
from src.models.admin import SERVICEPERSON
from src.models.db import BusinessDb, Default
from src.models.mode import STAGING_ONLY, Mode
from src.models.people import Account, OwnerAccount
from src.models.platform import (
    Business,
    BusinessCreate,
    BusinessPatch,
    BusinessPlan,
    BusinessStatus,
    Caller,
)
from src.models.subscription import SubscriptionRecord
from src.services.billing import BillingService
from src.services.checklist import ChecklistService
from src.services.errors import PlatformError
from src.services.modes import ByMode
from src.services.people.rules import holds
from src.services.provision import Provisioner
from src.tunables import tunable

ID_ALPHABET = string.ascii_lowercase + string.digits
#: What a business's own token may change: the owner's sign-off puts it live.
OWNER_FIELDS = {"configured", "status"}
#: The app a sign-up belongs to when it names none.
DEFAULT_APP = "soneeva"
#: A business's database is `{DB_PREFIX}{businessId}`; soneeva-voice assumes the same.
DB_PREFIX = "b-"

def new_business_id() -> str:
    """Lowercase, starts with a letter: valid inside a Firestore database id."""
    rest = "".join(secrets.choice(ID_ALPHABET) for _ in range(tunable("businesses.idLength") - 1))
    return secrets.choice(string.ascii_lowercase) + rest

async def audit(db: Database, action: str, business_id: str, by: str, data: dict[str, Any]) -> None:
    await db.set_row(Default.AUDIT, uuid.uuid4().hex, {
        "action": action, "businessId": business_id, "by": by, "data": data,
        "at": datetime.now(UTC),
    })

class BusinessService:
    def __init__(self, db: Database, accounts: AccountProvider,
                 billing: ByMode[BillingService] | None = None,
                 identity: PasswordSignIn | None = None) -> None:
        self._db = db
        self.checklist = ChecklistService(db)
        self._accounts = accounts
        self._billing = billing
        #: Checks a resumed password sign-up's password; without it, an existing email is refused.
        self.identity = identity
        self.provisioner = Provisioner(db, accounts, self.checklist)

    async def with_plan(self, business: Business, record: SubscriptionRecord | None = None) -> Business:
        """`plan` as the API shows it, {code, status, active}, from the Stripe subscription."""
        if not self._billing:
            return business
        record = record or await self._billing[business.mode].get_record(business.appId, business.id)
        plan = None if record.status == "none" else BusinessPlan(
            code=record.plan_code, status=record.status, active=record.is_active)
        return business.model_copy(update={"plan": plan})

    async def get(self, business_id: str) -> Business | None:
        row = await self._db.get_row(Default.BUSINESSES, business_id)
        return Business(**row) if row else None

    async def require(self, caller: Caller | None, business_id: str) -> Business:
        business = await self.get(business_id)
        if business and caller and caller.mode is Mode.TEST and not business.test:
            raise PlatformError(STAGING_ONLY, 403)
        if not business or (caller and not caller.reaches(business)):
            raise PlatformError("No such business", 404)
        return business

    async def every(self) -> list[Business]:
        return [Business(**row) for row in await self._db.get_rows(Default.BUSINESSES)]

    async def visible(
        self, caller: Caller, status: str | None = None, assigned: bool = False
    ) -> list[Business]:
        """`assigned`: only those whose serviceperson is the caller (a person holding Businesses.Access).
        Deleted businesses only when `status` asks for them."""
        filters: dict[str, Any] = {"status": status} if status else {}
        if assigned:
            access = holds(caller.person_scopes or [], "Businesses.Access")
            if not caller.uid or caller.business_id or not access:
                raise PlatformError("assigned=me needs a person holding Businesses.Access", 403)
            filters["servicepersonUid"] = caller.uid
        rows = await self._db.get_rows(Default.BUSINESSES, filters or None)
        found = [b for b in (Business(**r) for r in rows) if caller.reaches(b)
                 and (status or b.status is not BusinessStatus.DELETED)]
        if not self._billing:
            return found
        records, none = await self.subscriptions(found), SubscriptionRecord(app_id="", tenant="")
        return [await self.with_plan(b, records.get(b.id, none)) for b in found]

    async def subscriptions(self, found: list[Business]) -> dict[str, SubscriptionRecord]:
        """Each business's newest subscription by id: one Stripe listing per app and mode."""
        if not self._billing:
            return {}
        pairs = {(b.mode, b.appId) for b in found}
        listed = {(mode, app): await self._billing[mode].records(app) for mode, app in pairs}
        return {b.id: r for b in found if (r := listed[b.mode, b.appId].get(b.id))}

    async def branches(self, business: Business) -> list[dict[str, Any]]:
        rows = await self.business_db(business).get_rows(BusinessDb.BRANCHES)
        return [{"id": r["id"], "name": r.get("name", ""), "phone": r.get("phone", ""),
                 "address": r.get("address", "")} for r in rows]

    async def create(self, body: BusinessCreate, by: str, mode: Mode = Mode.LIVE) -> Business:
        """The owner's account (with `owner`) and the pending business row; `provisioner` builds the
        rest afterwards. Idempotent per owner: a retry returns the same business, and a password
        sign-up whose account a half-finished sign-up left behind resumes when the password matches.
        An account made here is removed again when its row can't be written. A staging caller's
        business is `test` for good."""
        if body.type not in tunable("businesses.types"):
            raise PlatformError(f"Unknown business type '{body.type}'", 422)
        if not body.owner:
            owner = await self._owner(body.ownerUid or "")
            if body.ownerUsername:
                owner = owner.model_copy(update={"username": body.ownerUsername})
            business = await self._record(body, owner, mode)
        else:
            owner, made = await self._signup_account(body.owner)
            try:
                business = await self._record(body.model_copy(update={"ownerUid": owner.uid}), owner, mode)
            except BaseException:
                if made:
                    await self._accounts.delete(owner.uid)
                raise
        await audit(self._db, "business.create", business.id, by, {"name": business.name})
        return business

    async def _signup_account(self, owner: OwnerAccount) -> tuple[Account, bool]:
        """The new account and True, or the unfinished one already there and False."""
        email = owner.email.strip().lower()
        try:
            account = await self._accounts.create(email, owner.password, owner.displayName,
                                                  {"username": owner.username})
            return account, True
        except AccountError as error:
            unfinished = await self._unfinished(email, owner.password) \
                if error.code == "email-exists" else None
            if not unfinished:
                raise PlatformError(str(error), error.status) from error
            return unfinished, False

    async def _unfinished(self, email: str, password: str) -> Account | None:
        """The account behind `email` and `password` when it owns no provisioned business, belongs to
        no other business and is no platform person: what an interrupted sign-up leaves."""
        uid = await self.identity.uid(email, password) if self.identity else None
        account = await self._accounts.get(uid) if uid else None
        if not account or await self._db.get(Default.USERS, account.uid) is not None:
            return None
        mine = await self._db.get_rows(Default.BUSINESSES, {"ownerUid": account.uid}, limit=1)
        if mine:
            return None if mine[0].get("provisioned") else account
        return None if account.businessId else account

    async def _owner(self, uid: str) -> Account:
        account = await self._accounts.get(uid) if uid else None
        if not account:
            raise PlatformError("Owner account not found", 404)
        if await self._db.get(Default.USERS, uid) is not None:
            raise PlatformError("A platform account can't own a business", 403)
        return account

    async def _record(self, body: BusinessCreate, owner: Account, mode: Mode) -> Business:
        """The owner's business row, written once (pending, not provisioned)."""
        mine = await self._db.get_rows(Default.BUSINESSES, {"ownerUid": owner.uid}, limit=1)
        if mine:
            business = Business(**mine[0])
            if business.mode is not mode:
                raise PlatformError(f"This owner's business is in Stripe {business.mode} mode", 409)
            return business
        if owner.businessId:
            raise PlatformError("This account already belongs to a business", 409)
        business_id = new_business_id()
        business = Business(
            id=business_id, name=body.name.strip(), type=body.type,
            appId=body.appId or DEFAULT_APP, dbId=f"{DB_PREFIX}{business_id}",
            ownerUid=owner.uid, test=mode is Mode.TEST, country=body.country.upper(),
            timezone=body.timezone, languages=body.languages, branchCount=body.branchCount,
            phone=body.phone, email=body.email or owner.email, consentAt=body.consentAt,
            createdAt=datetime.now(UTC),
        )
        signup = {"address": body.address, "hours": body.hours.model_dump(), "username": owner.username}
        await self._db.set_row(Default.BUSINESSES, business_id,
                               business.model_dump(mode="json", exclude={"id", "plan"}) | {"signup": signup})
        return business

    async def patch(self, caller: Caller, business_id: str, patch: BusinessPatch) -> Business:
        """Pause is status `onhold`; delete is status `deleted` (and restoring from it), which needs
        Businesses.Delete and is never the business's own to make."""
        before = await self.require(caller, business_id)
        fields = patch.model_dump(exclude_unset=True, mode="json")
        deleting = "status" in fields and BusinessStatus.DELETED in (fields["status"], before.status)
        if caller.business_id and (deleting or not set(fields) <= OWNER_FIELDS):
            raise PlatformError("A business may only change configured and status", 403)
        role = caller.person_scopes
        if role is not None:
            need = {"Businesses.Assign" if f == "servicepersonUid"
                    else "Businesses.Delete" if f == "status" and deleting else "Businesses.Write"
                    for f in fields}
            missing = sorted(n for n in need if not holds(role, n))
            if missing:
                raise PlatformError(f"Requires {', '.join(missing)}", 403)
        uid = fields.get("servicepersonUid")
        if uid:
            person = await self._db.get(Default.USERS, uid) or {}
            if person.get("role") != SERVICEPERSON or person.get("disabled") is True:
                raise PlatformError("No active serviceperson with that uid", 422)
        if fields:
            await self._db.set_row(Default.BUSINESSES, business_id, fields)
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
        await self.set_fields(business_id, {"status": BusinessStatus.DELETED.value})
        await audit(self._db, "business.delete", business_id, by, {"name": business.name})
        return business

    async def set_fields(self, business_id: str, fields: dict[str, Any]) -> None:
        await self._db.set_row(Default.BUSINESSES, business_id, fields)

    async def database(self, business_id: str) -> Database | None:
        row = await self._db.get_row(Default.BUSINESSES, business_id)
        return self._db.open(row["dbId"]) if row and row.get("dbId") else None

    def business_db(self, business: Business) -> Database:
        return self._db.open(business.dbId)
