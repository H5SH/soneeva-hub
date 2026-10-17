"""/v1/roles: one role holds any mix of console ids (`Users.View`, `*`) and `payment.*`/`platform.*`.

Never `hub.*`: those are app scopes, kept on apps and tools. Nobody gives a role a scope they don't
hold (taking one off is always fine), edits their own role, or deletes a role someone holds.
"""

from typing import Any

from src.models.admin import person_scopes
from src.models.people import Actor
from src.services.people.rules import Refused, assignable, catalogue, covers, holds, known, reach
from src.services.people.store import People
from src.services.roles import RoleStore

VIEW, WRITE = "Roles.View", "Roles.Write"
#: Reading roles: the roles pages, and the people pages that give them.
READERS = (VIEW, WRITE, "Users.View", "Users.Create", "Users.Role.Write")


def role_name(name: str) -> str:
    clean = name.strip().lower().replace(" ", "-")
    if not clean or not clean.replace("-", "").replace("_", "").isalnum():
        raise Refused("Use letters, numbers, hyphens and underscores only.", 422)
    return clean


class RoleService:
    def __init__(self, people: People, cache: RoleStore) -> None:
        self._people = people
        self._cache = cache

    async def overview(self, actor: Actor) -> dict[str, Any]:
        """Every role with its holders, the scope catalogue, and the roles the actor may give."""
        if not actor.platform or not any(reach(actor.scopes, s) for s in READERS):
            raise Refused("You can't see roles")
        roles = await self._people.roles()
        return {
            "roles": [{"name": name, "scopes": scopes, "holders": await self._people.holders(name)}
                      for name, scopes in sorted(roles.items())],
            "scopes": catalogue(),
            "assignable": assignable(actor.scopes, roles),
        }

    def _check(self, actor: Actor, name: str, scopes: list[str], had: list[str]) -> list[str]:
        if not actor.platform or not holds(actor.scopes, WRITE):
            raise Refused("You can't change roles")
        if name == actor.role:
            raise Refused("You can't change your own role")
        scopes = list(dict.fromkeys(person_scopes(scopes)))
        added = [s for s in scopes if s not in had]
        ids = known()
        unknown = [s for s in added if s not in ids]
        if unknown:
            raise Refused(f"Unknown scope(s): {', '.join(unknown)}", 422)
        if not all(covers(actor.scopes, s) for s in added):
            raise Refused("You can't give scopes you don't hold")
        return scopes

    async def create(self, actor: Actor, name: str, scopes: list[str]) -> dict[str, Any]:
        name = role_name(name)
        if name in await self._people.roles():
            raise Refused(f"A role called '{name}' already exists.", 409)
        await self._save(name, self._check(actor, name, scopes, []))
        return await self.overview(actor)

    async def update(self, actor: Actor, name: str, scopes: list[str]) -> dict[str, Any]:
        """Replaces the role's scopes; any `hub.*` sent or still stored falls away."""
        had = (await self._people.roles()).get(name)
        if had is None:
            raise Refused("No such role", 404)
        await self._save(name, self._check(actor, name, scopes, had))
        return await self.overview(actor)

    async def delete(self, actor: Actor, name: str) -> dict[str, Any]:
        if name not in await self._people.roles():
            raise Refused("No such role", 404)
        self._check(actor, name, [], [])
        holders = await self._people.holders(name)
        if holders:
            raise Refused(f"{holders} person(s) still have the {name} role. Move them off it first.", 409)
        await self._save(name, None)
        return await self.overview(actor)

    async def _save(self, name: str, scopes: list[str] | None) -> None:
        await self._people.save_roles({name: scopes})
        self._cache.forget(name)
