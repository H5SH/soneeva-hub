"""Private images at fixed paths in the bucket: the path is the address, nothing stores a URL.

`MEDIA` is the whole rulebook: each path pattern with who may see it and who may change it.
"""

import re
from collections.abc import Awaitable, Callable

from src.auth import AccountProvider
from src.models import paths
from src.models.admin import SERVICEPERSON, Scope
from src.models.identity import Identity
from src.services.advertisements import AdvertisementService
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.people.rules import PROFILE_EDIT, PROFILE_WRITE, holds, scope_reach
from src.services.people.store import People

USERS_VIEW, USERS_WRITE = "Users.View", "Users.Write"
BUSINESSES_VIEW, BUSINESSES_ACCESS = "Businesses.View", "Businesses.Access"
ADVERTISE_VIEW, ADVERTISE_WRITE = "Advertise.View", "Advertise.Write"
SERVICEPERSONS_VIEW = "Servicepersons.View"
AUDIO_VIEW = ("ivr.view", "ivr.edit", "audio.edit")


BRANCH_VIEW = ("branches.view", "branches.edit")


def _on_platform(who: Identity, scope: str) -> bool:
    return who.platform and holds(who.scopes, scope)


class MediaRules:
    def __init__(self, accounts: AccountProvider, people: People, businesses: BusinessService,
                 ads: AdvertisementService) -> None:
        self._accounts = accounts
        self._people = people
        self._businesses = businesses
        self._ads = ads

    async def _business_of(self, uid: str) -> str | None:
        account = await self._accounts.get(uid)
        return account.businessId if account else None

    async def _reads_business(self, who: Identity, business_id: str) -> bool:
        """Businesses.View, or Businesses.Access on a business assigned to the person."""
        if _on_platform(who, BUSINESSES_VIEW):
            return True
        if not _on_platform(who, BUSINESSES_ACCESS):
            return False
        business = await self._businesses.get(business_id)
        return business is not None and business.servicepersonUid == who.uid

    async def _platform_reach(self, who: Identity, scope: str, uid: str) -> bool:
        """`scope` over a platform account: everyone, or (an `Assigned.` twin) those they look after."""
        r = scope_reach(who.scopes, scope) if who.platform else None
        if not r or await self._business_of(uid):
            return False
        return r == "all" or (who.uid, uid) in await self._people.assignments()

    async def _in_business_of(self, who: Identity, uid: str) -> bool:
        if not who.business_id or not who.inside(who.business_id):
            return False
        if await self._business_of(uid) == who.business_id:
            return True
        business = await self._businesses.get(who.business_id)
        return business is not None and business.servicepersonUid == uid

    async def avatar_view(self, who: Identity, uid: str) -> bool:
        if uid == who.uid:
            return True
        if who.business_id:
            return await self._in_business_of(who, uid)
        if await self._platform_reach(who, USERS_VIEW, uid):
            return True
        if not _on_platform(who, SERVICEPERSONS_VIEW):
            return False
        return (await self._people.profile(uid) or {}).get("role") == SERVICEPERSON

    async def avatar_edit(self, who: Identity, uid: str) -> bool:
        if uid == who.uid:
            return who.has_exact(PROFILE_EDIT) if who.business_id else _on_platform(who, PROFILE_WRITE)
        if who.business_id:
            return who.has_exact("team.edit") and await self._business_of(uid) == who.business_id
        return await self._platform_reach(who, USERS_WRITE, uid)

    async def icon_view(self, who: Identity, app_id: str) -> bool:
        return who.may(Scope.APP_VIEW, app_id)

    async def icon_edit(self, who: Identity, app_id: str) -> bool:
        return who.may(Scope.APP_UPDATE, app_id)

    async def logo_view(self, who: Identity, business_id: str) -> bool:
        if who.business_id:
            return who.inside(business_id)
        if who.has(Scope.BUSINESS_VIEW) and who.reaches_id(business_id):
            return True
        return await self._reads_business(who, business_id)

    async def logo_edit(self, who: Identity, business_id: str) -> bool:
        return who.business_id == business_id and who.has_exact("business.edit")

    async def branch_logo_view(self, who: Identity, business_id: str) -> bool:
        if who.business_id:
            return who.business_id == business_id and any(who.has_exact(s) for s in BRANCH_VIEW)
        return await self._reads_business(who, business_id)

    async def branch_logo_edit(self, who: Identity, business_id: str) -> bool:
        return who.business_id == business_id and who.has_exact("branches.edit")

    async def audio_view(self, who: Identity, business_id: str) -> bool:
        if who.business_id:
            return who.business_id == business_id and any(who.has_exact(s) for s in AUDIO_VIEW)
        return await self.logo_view(who, business_id)

    async def ad_view(self, who: Identity, ad_id: str) -> bool:
        """Advertise.View (or .Write), or a business the ad is published to."""
        if who.business_id:
            return who.inside(who.business_id) and await self._ads.published_to(who.business_id, ad_id)
        return _on_platform(who, ADVERTISE_VIEW) or _on_platform(who, ADVERTISE_WRITE)

    async def ad_edit(self, who: Identity, _ad_id: str) -> bool:
        return not who.business_id and _on_platform(who, ADVERTISE_WRITE)

    async def audio_edit(self, who: Identity, business_id: str) -> bool:
        return who.business_id == business_id and who.has_exact("audio.edit")


Rule = Callable[[MediaRules, Identity, str], Awaitable[bool]]

#: Every path pattern (src/models/paths.py) with who may see it and who may change it, each given the
#: pattern's first group: the person, app, business or ad it belongs to. The caches
#: soneeva-voice keeps (`businesses/{id}/cache/…`, `cache/…`) and the demo's `audio/` are not media.
MEDIA: list[tuple[re.Pattern[str], Rule, Rule]] = [
    (paths.USER_AVATAR, MediaRules.avatar_view, MediaRules.avatar_edit),
    (paths.APP_AVATAR, MediaRules.icon_view, MediaRules.icon_edit),
    (paths.BUSINESS_AVATAR, MediaRules.logo_view, MediaRules.logo_edit),
    (paths.BRANCH_AVATAR, MediaRules.branch_logo_view, MediaRules.branch_logo_edit),
    (paths.AUDIO, MediaRules.audio_view, MediaRules.audio_edit),
    (paths.AD_IMAGE, MediaRules.ad_view, MediaRules.ad_edit),
]


def audio_target(path: str) -> tuple[str, str, int | None] | None:
    """(business, name, version or None for active) when the path is a recording."""
    match = paths.AUDIO.match(path)
    if not match:
        return None
    business_id, name, which = match.groups()
    return business_id, name, None if which == "active" else int(which[1:])


async def check(rules: MediaRules, who: Identity, path: str, edit: bool) -> None:
    """Raises 404 for a path that is not media, 403 when the person may not see or change it."""
    for pattern, view, change in MEDIA:
        match = pattern.match(path)
        if match:
            if not await (change if edit else view)(rules, who, match.group(1)):
                raise PlatformError("You can't " + ("change" if edit else "see") + " this file", 403)
            return
    raise PlatformError("Not a media path", 404)
