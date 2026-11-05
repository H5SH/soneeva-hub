"""Sign-up: the owner's account and the pending business row; src/services/provision.py builds the rest."""

import secrets
import string
from datetime import UTC, datetime

from src.auth import AccountError, AccountProvider
from src.auth.password import PasswordSignIn
from src.db import Database
from src.models.db import Default
from src.models.mode import SONEEVA, Mode
from src.models.people import Account, OwnerAccount, StoreOwner
from src.models.platform import Business, BusinessCreate
from src.services.audit import audit
from src.services.errors import PlatformError
from src.services.regions import DataRegions
from src.services.store_rows import claim_store
from src.tunables import tunable

ID_ALPHABET = string.ascii_lowercase + string.digits
STORE_TAKEN = "This store already has a business"


def new_business_id() -> str:
    """Lowercase, starts with a letter: valid inside a Firestore database id."""
    rest = "".join(secrets.choice(ID_ALPHABET) for _ in range(tunable("businesses.idLength") - 1))
    return secrets.choice(string.ascii_lowercase) + rest


class Signups:
    def __init__(self, db: Database, accounts: AccountProvider, regions: DataRegions,
                 identity: PasswordSignIn | None) -> None:
        self._db = db
        self._accounts = accounts
        self.regions = regions
        #: Checks a resumed password sign-up's password; without it, an existing email is refused.
        self.identity = identity

    async def create(self, body: BusinessCreate, by: str, mode: Mode = Mode.LIVE) -> Business:
        """The owner's account (with `owner`) and the pending business row; `provisioner` builds the
        rest afterwards. Idempotent per owner: a retry returns the same business, and a password
        sign-up whose account a half-finished sign-up left behind resumes when the password matches.
        An account made here is removed again when its row can't be written. A staging caller's
        business is `test` for good. A store with a business already is refused (409): sign in instead."""
        if body.type not in tunable("businesses.types"):
            raise PlatformError(f"Unknown business type '{body.type}'", 422)
        if body.appId and not await self._db.get_row(Default.APPS, body.appId):
            raise PlatformError(f"Unknown app '{body.appId}'", 422)
        store = body.store
        test = mode is Mode.TEST
        if store and await self._db.get_row(Default.STORES, store.doc(test)):
            raise PlatformError(STORE_TAKEN, 409, "store_taken")
        if not body.owner:
            owner = await self._owner(body.ownerUid or "")
            if body.ownerUsername:
                owner = owner.model_copy(update={"username": body.ownerUsername})
            business = await self._record(body, owner, mode)
        else:
            owner, made = await self._signup_account(body.owner)
            recorded = None
            try:
                recorded = await self._record(body.model_copy(update={"ownerUid": owner.uid}), owner, mode)
                if store and not await claim_store(self._db, store, recorded.id, test):
                    raise PlatformError(STORE_TAKEN, 409, "store_taken")
                business = recorded
            except BaseException:
                if made:
                    await self._accounts.delete(owner.uid)
                if store and recorded:  # a store's owner is always new, so its row is too
                    await self._db.delete_row(Default.BUSINESSES, recorded.id)
                raise
        await audit(self._db, "business.create", business.id, by, {"name": business.name})
        return business

    async def _signup_account(self, owner: OwnerAccount | StoreOwner) -> tuple[Account, bool]:
        """The new account and True, or the unfinished one already there and False. A store's owner gives
        no password to prove an account theirs, so an existing email is refused: they sign in to connect."""
        email = owner.email.strip().lower()
        password = owner.password if isinstance(owner, OwnerAccount) else None
        try:
            account = await self._accounts.create(email, password or secrets.token_urlsafe(32),
                                                  owner.displayName, {"username": owner.username})
            return account, True
        except AccountError as error:
            unfinished = await self._unfinished(email, password) \
                if password and error.code == "email-exists" else None
            if not unfinished:
                raise PlatformError(str(error), error.status, error.code) from error
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
        db_id, location = await self.regions.choose(
            business_id, body.country, body.dataRegion, body.dataLocation)
        business = Business(
            id=business_id, name=body.name.strip(), type=body.type,
            appId=body.appId or SONEEVA, dbId=db_id, dataLocation=location,
            ownerUid=owner.uid, test=mode is Mode.TEST, country=body.country.upper(),
            timezone=body.timezone, languages=body.languages, branchCount=body.branchCount,
            phone=body.phone, email=body.email or owner.email, consentAt=body.consentAt,
            createdAt=datetime.now(UTC),
        )
        signup = {"address": body.address, "hours": body.hours.model_dump(), "username": owner.username}
        await self._db.upsert_live(Default.BUSINESSES, business_id,
                               business.model_dump(mode="json", exclude={"id", "plan"}) | {"signup": signup})
        return business
