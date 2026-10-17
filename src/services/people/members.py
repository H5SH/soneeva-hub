"""/v1/accounts: a business's members as the console manages them (business token, `team.*` in that
business), each person's own name and password, and the signed-out reset request. Platform people
are /v1/people (PeopleService)."""

import logging

from src.auth import AccountError, AccountProvider
from src.models.people import Account, AccountCreate, AccountPatch, Actor
from src.services.people.rules import PROFILE_EDIT, Refused
from src.services.rate_limit import RateLimit
from src.tunables import tunable

TEAM_EDIT, TEAM_VIEW = "team.edit", "team.view"
SELF_FIELDS = {"displayName", "password"}
logger = logging.getLogger(__name__)


class MemberService:
    def __init__(self, accounts: AccountProvider) -> None:
        self._accounts = accounts
        self._recoveries = RateLimit(tunable("accounts.resetLimit"), tunable("accounts.resetWindowSeconds"))

    @staticmethod
    def _business(actor: Actor) -> str:
        if not actor.business_id:
            raise Refused("Call as a business; platform people are /v1/people")
        return actor.business_id

    async def _member(self, actor: Actor, uid: str, *scopes: str) -> Account:
        """The account, in the actor's business, when the actor holds one of `scopes` there."""
        business = self._business(actor)
        account = await self._accounts.get(uid)
        if not account or account.businessId != business:
            raise Refused("No such account", 404)
        if scopes and not any(s in actor.scopes for s in scopes):
            raise Refused(f"Requires {scopes[-1]} in this business")
        return account

    async def get(self, actor: Actor, uid: str) -> Account:
        if uid == actor.uid:
            self._business(actor)
            account = await self._accounts.get(uid)
            if not account:
                raise Refused("No such account", 404)
            return account
        return await self._member(actor, uid, TEAM_VIEW, TEAM_EDIT)

    async def create(self, actor: Actor, body: AccountCreate) -> Account:
        business = self._business(actor)
        if TEAM_EDIT not in actor.scopes:
            raise Refused(f"Requires {TEAM_EDIT} in this business")
        if body.businessId not in (None, business):
            raise Refused("A business token creates members of its own business only")
        try:
            return await self._accounts.create(body.email, body.password, body.displayName,
                                               {"username": body.username, "businessId": business})
        except AccountError as error:
            raise Refused(str(error), error.status) from error

    async def update(self, actor: Actor, uid: str, patch: AccountPatch) -> Account:
        fields = patch.model_dump(exclude_unset=True)
        own = uid == actor.uid and fields and set(fields) <= SELF_FIELDS
        if own and PROFILE_EDIT in actor.scopes:
            self._business(actor)
        else:
            await self._member(actor, uid, TEAM_EDIT)
            if {"email", "password"} & set(fields):
                raise Refused("Only the person changes their own email and password")
        try:
            if "username" in fields:
                await self._accounts.set_claims(uid, {"username": fields.pop("username")})
            if fields:
                await self._accounts.update(uid, fields)
        except AccountError as error:
            raise Refused(str(error), error.status) from error
        return await self.get(actor, uid) if uid == actor.uid else await self._member(actor, uid)

    async def delete(self, actor: Actor, uid: str) -> None:
        await self._member(actor, uid, TEAM_EDIT)
        await self._accounts.delete(uid)

    async def recover(self, email: str) -> None:
        """Firebase's reset email to `email`, if it has an account; never says which."""
        key = email.strip().lower()
        if not self._recoveries.allow(key):
            return
        try:
            await self._accounts.send_reset(key)
        except AccountError as error:
            logger.info("No reset email sent: %s", error)
