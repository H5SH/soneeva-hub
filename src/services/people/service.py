"""Platform people (/v1/people): one record per person, holding their role, payment apps, business
limit, token allowance and who looks after them. Every method takes the resolved Actor and refuses with
rules.Refused; nobody grants what they don't hold or edits themselves into more power."""

import asyncio
import secrets

from src.auth import AccountError, AccountProvider
from src.models.admin import SERVICEPERSON
from src.models.people import Actor, Person, PersonCreate, PersonPatch
from src.models.platform import Serviceperson
from src.services.people.rules import (
    PROFILE_WRITE,
    Refused,
    assignable,
    covers,
    held,
    holds,
    is_serviceperson_role,
    plan_tokens,
    reach,
)
from src.services.people.store import People
from src.tunables import tunable

SELF_FIELDS = {"displayName", "password", "phone"}
ACCOUNT_FIELDS = {"email", "password", "displayName", "username"}
#: On the person's record, not the account; changed with Users.Write.
CONTACT_FIELDS = {"phone"}
ACCESS_FIELDS = {"role", "apps", "businesses"}


class PeopleService:
    def __init__(self, accounts: AccountProvider, people: People) -> None:
        self._accounts = accounts
        self.store = people

    # ── reading ──────────────────────────────────────────────────────────────
    async def _with_profile(self, accounts: list) -> list[Person]:
        profiles = await self.store.profiles([a.uid for a in accounts])
        grants = await self.store.assignments()
        out = []
        for a in accounts:
            p = profiles.get(a.uid, {})
            out.append(Person(**a.model_dump(), role=p.get("role"), tokens=p.get("tokens"),
                              phone=p.get("phone", ""),
                              apps=p.get("apps", []), businesses=p.get("businesses", []),
                              servicepersons=[sp for sp, user in grants if user == a.uid]))
        return out

    @staticmethod
    def _may_read(actor: Actor) -> bool:
        return actor.platform and any(reach(actor.scopes, s) for s in tunable("people.readScopes"))

    async def page(self, actor: Actor, token: str | None) -> tuple[list[Person], str | None]:
        if not self._may_read(actor):
            raise Refused("You can't see platform people")
        accounts, nxt = await self._accounts.page(token)
        return await self._with_profile([a for a in accounts if not a.businessId]), nxt

    async def everyone(self) -> list[Person]:
        """Every platform person, all pages (the home page counts them)."""
        found, token = [], None
        while True:
            accounts, token = await self._accounts.page(token)
            found += [a for a in accounts if not a.businessId]
            if not token:
                return await self._with_profile(found)

    async def load(self, uid: str) -> Person:
        account = await self._accounts.get(uid)
        if not account or account.businessId:
            raise Refused("No such person", 404)
        return (await self._with_profile([account]))[0]

    async def servicepersons(self, uid: str | None = None) -> list[Serviceperson]:
        """People whose role is `serviceperson` (or just `uid`, if it is one) as contact cards."""
        found = await self.store.with_role(SERVICEPERSON)
        uids = [u for u in sorted(found) if uid in (None, u)]
        accounts = await asyncio.gather(*[self._accounts.get(u) for u in uids])
        return [Serviceperson(uid=a.uid, name=a.displayName or a.email, phone=found[a.uid]["phone"],
                              email=a.email, active=not (a.disabled or found[a.uid]["disabled"]))
                for a in accounts if a and not a.businessId]

    async def get(self, actor: Actor, uid: str) -> Person:
        if uid != actor.uid and not self._may_read(actor):
            raise Refused("No such person", 404)
        return await self.load(uid)

    # ── rules ────────────────────────────────────────────────────────────────
    @staticmethod
    def _can_on(actor: Actor, scope: str, target: Person) -> None:
        r = reach(actor.scopes, scope)
        mine = target.uid == actor.uid or actor.uid in target.servicepersons
        if r == "all" or (r == "assigned" and mine):
            return
        raise Refused(f"Requires {scope} on this person")

    async def _target(self, actor: Actor, uid: str) -> Person:
        if not actor.platform:
            raise Refused("Only platform people manage platform people")
        return await self.load(uid)

    async def _servicepersons(self, uids: list[str]) -> None:
        roles = await self.store.roles()
        profiles = await self.store.profiles(uids)
        role_of = {u: profiles.get(u, {}).get("role") or "" for u in uids}
        unknown = [u for u in uids if not is_serviceperson_role(roles.get(role_of[u], []))]
        if unknown:
            raise Refused(f"Not a serviceperson: {', '.join(unknown)}", 422)

    async def _give_role(self, actor: Actor, role: str, current: str | None = None) -> None:
        roles = await self.store.roles()
        if role not in roles:
            raise Refused(f"There is no role called {role}", 422)
        allowed = assignable(actor.scopes, roles)
        if role not in allowed or (current and current not in allowed):
            raise Refused(f"You can't give the {role} role")

    @staticmethod
    def _give_apps(actor: Actor, apps: list[str]) -> None:
        if not actor.all_apps and not set(apps) <= set(actor.apps):
            raise Refused("You can't give apps you aren't on")

    # ── writing ──────────────────────────────────────────────────────────────
    async def create(self, actor: Actor, body: PersonCreate) -> tuple[Person, str | None]:
        """Returns the person and, when no password was given, a link to set one (shown once)."""
        role, servicepersons, tokens = await self._plan_create(actor, body)
        email = body.email.strip().lower()
        try:
            account = await self._accounts.create(email, body.password or secrets.token_urlsafe(32),
                                                  body.displayName, {"username": body.username})
        except AccountError as error:
            raise Refused(str(error), error.status) from error
        try:
            await self.store.save(account.uid, {"email": email, "role": role, "apps": body.apps,
                                                "phone": body.phone, "businesses": body.businesses,
                                                "disabled": False})
            if servicepersons:
                await self.store.set_assignment(account.uid, servicepersons)
            r = reach(actor.scopes, "Users.Tokens.Write")
            await self.store.transfer(account.uid, tokens, actor.uid if r == "assigned" else None,
                                      plan_tokens(r, False, tokens))
        except Exception:
            await self._forget(account.uid)
            raise
        link = None if body.password else await self._link(email)
        return await self.load(account.uid), link

    async def _plan_create(self, actor: Actor, body: PersonCreate):
        if not actor.platform or not reach(actor.scopes, "Users.Create"):
            raise Refused("You can't create people")
        roles = await self.store.roles()
        may_set = reach(actor.scopes, "Users.Role.Write") is not None
        role = body.role if may_set and body.role else None
        if role is None:
            options = sorted(assignable(actor.scopes, roles), key=lambda r: len(roles[r]))
            role = options[0] if options else None
        if not role:
            raise Refused("There is no role you can give")
        await self._give_role(actor, role)
        if (body.apps or body.businesses) and not may_set:
            raise Refused("You can't set apps or businesses")
        self._give_apps(actor, body.apps)
        given = body.servicepersons
        may_assign = covers(actor.scopes, "Users.Assign.Write") and given is not None
        servicepersons = given if may_assign else [actor.uid]
        if given and servicepersons == given:
            await self._servicepersons(given)
        can_give = reach(actor.scopes, "Users.Tokens.Write") is not None
        tokens = (body.tokens if "tokens" in body.model_fields_set else 0) if can_give else 0
        return role, servicepersons, tokens

    async def _link(self, email: str) -> str | None:
        try:
            return await self._accounts.reset_link(email)
        except AccountError:
            return None

    async def _forget(self, uid: str) -> None:
        await self.store.remove(uid)
        await self.store.revoke_all(uid)
        await self._accounts.delete(uid)

    @staticmethod
    def _edits_self(actor: Actor, uid: str, fields: dict) -> bool:
        """Your own name and password, with Profile.Write."""
        return (uid == actor.uid and bool(fields) and set(fields) <= SELF_FIELDS and actor.platform
                and holds(actor.scopes, PROFILE_WRITE))

    async def _check_update(self, actor: Actor, uid: str, fields: dict) -> Person:
        target = await self._target(actor, uid)
        self._can_on(actor, "Users.View", target)
        if (ACCOUNT_FIELDS | CONTACT_FIELDS) & set(fields):
            self._can_on(actor, "Users.Write", target)
        if "disabled" in fields:
            self._can_on(actor, "Users.Disable", target)
            if uid == actor.uid:
                raise Refused("You can't disable yourself")
        changed = {k for k in ACCESS_FIELDS & set(fields) if fields[k] != getattr(target, k)}
        if changed:
            self._can_on(actor, "Users.Role.Write", target)
            if uid == actor.uid:
                raise Refused("You can't change your own role, apps or businesses")
        if "role" in changed:
            await self._give_role(actor, fields["role"], target.role)
        if "apps" in changed:
            self._give_apps(actor, fields["apps"])
        return target

    async def update(self, actor: Actor, uid: str, patch: PersonPatch) -> tuple[Person, int]:
        fields = patch.model_dump(exclude_unset=True, exclude_none=True)
        own = self._edits_self(actor, uid, fields)
        target = None if own else await self._check_update(actor, uid, fields)
        try:
            if "username" in fields:
                await self._accounts.set_claims(uid, {"username": fields["username"]})
            account = {k: v for k, v in fields.items() if k in ACCOUNT_FIELDS - {"username"} | {"disabled"}}
            if account:
                await self._accounts.update(uid, account)
        except AccountError as error:
            raise Refused(str(error), error.status) from error
        kept = ACCESS_FIELDS | CONTACT_FIELDS | {"disabled", "email"}
        record = {k: v for k, v in fields.items() if k in kept}
        if record:
            await self.store.save(uid, record)
        dropped = 0
        if target and fields.get("role", target.role) != target.role:
            roles = await self.store.roles()
            if is_serviceperson_role(roles.get(target.role or "", [])) and not is_serviceperson_role(
                    roles[fields["role"]]):
                dropped = await self.store.revoke_all(uid)
        return await self.load(uid), dropped

    async def delete(self, actor: Actor, uid: str) -> int:
        target = await self._target(actor, uid)
        self._can_on(actor, "Users.Delete", target)
        if uid == actor.uid:
            raise Refused("You can't delete yourself")
        refund = held(target.tokens) if actor.tokens is not None and actor.uid in target.servicepersons else 0
        await self._forget(uid)
        await self.store.add_tokens(actor.uid, refund)
        return refund

    async def reset(self, actor: Actor, uid: str, send: bool) -> dict:
        target = await self._target(actor, uid)
        self._can_on(actor, "Users.ResetPassword", target)
        try:
            if send:
                await self._accounts.send_reset(target.email)
                return {"sent": True}
            return {"link": await self._accounts.reset_link(target.email)}
        except AccountError as error:
            raise Refused(str(error), error.status) from error

    async def set_tokens(self, actor: Actor, uid: str, tokens: int | None) -> Person:
        target = await self._target(actor, uid)
        self._can_on(actor, "Users.Tokens.Write", target)
        r = reach(actor.scopes, "Users.Tokens.Write")
        await self.store.transfer(uid, tokens, actor.uid if r == "assigned" else None,
                                  plan_tokens(r, uid == actor.uid, tokens))
        return await self.load(uid)

    async def assign(self, actor: Actor, uid: str, servicepersons: list[str]) -> Person:
        target = await self._target(actor, uid)
        if not covers(actor.scopes, "Users.Assign.Write"):
            raise Refused("You can't change who looks after this person")
        self._can_on(actor, "Users.View", target)
        await self._servicepersons(servicepersons)
        await self.store.set_assignment(uid, sorted(set(servicepersons)))
        return await self.load(uid)
