"""The registry of storage paths: the one private bucket, every object karbon-hub or soneeva-voice keeps.

    users/{uid}/avatar
    apps/{appId}/avatar
    businesses/{id}/avatar
    businesses/{id}/branches/{branchId}/avatar
    businesses/{id}/audios/{name}/active | v{n}               client content, kept
    businesses/{id}/cache/greeting/{lang}/{mode}-{hash}.ulaw|.txt   soneeva-voice, rebuildable
    cache/fillers/{lang}/{hash}.ulaw                          soneeva-voice, shared, rebuildable
    audio/{lang}/{hash}.ulaw                                  demo-owned (the browser demo's fillers)
    advertisements/{adId}/icon | background                   karbon-admin's advertisement images

Paths carry no extension except the rebuildable caches; the type is metadata. Nothing else in the
hub spells a bucket path (tests/test_registry.py checks).
"""

import re

#: A path segment id: uid, app id, business id, branch id.
ID = r"[A-Za-z0-9_-]{1,128}"
#: An audio's name (its doc id in audios/): a slug.
AUDIO_NAME = r"[a-z0-9][a-z0-9-]{0,39}"


def user_avatar(uid: str) -> str:
    return f"users/{uid}/avatar"


def app_avatar(app_id: str) -> str:
    return f"apps/{app_id}/avatar"


def business_avatar(business_id: str) -> str:
    return f"businesses/{business_id}/avatar"


def branch_avatar(business_id: str, branch_id: str) -> str:
    return f"businesses/{business_id}/branches/{branch_id}/avatar"


def audio(business_id: str, name: str, version: int | None = None) -> str:
    """The active recording callers hear, or the earlier version `v{n}`."""
    return f"businesses/{business_id}/audios/{name}/" + (f"v{version}" if version else "active")


def advertisement_image(ad_id: str, kind: str) -> str:
    """An advertisement's `icon` or `background` image."""
    return f"advertisements/{ad_id}/{kind}"


def greeting_cache(business_id: str, lang: str, mode: str, digest: str, ext: str) -> str:
    """soneeva-voice's prepared greeting (`ext` ulaw or txt)."""
    return f"businesses/{business_id}/cache/greeting/{lang}/{mode}-{digest}.{ext}"


def filler_cache(lang: str, digest: str) -> str:
    """soneeva-voice's holding phrases, shared by every business."""
    return f"cache/fillers/{lang}/{digest}.ulaw"


USER_AVATAR = re.compile(rf"^users/({ID})/avatar$")
APP_AVATAR = re.compile(rf"^apps/({ID})/avatar$")
BUSINESS_AVATAR = re.compile(rf"^businesses/({ID})/avatar$")
BRANCH_AVATAR = re.compile(rf"^businesses/({ID})/branches/({ID})/avatar$")
AD_IMAGE = re.compile(rf"^advertisements/({ID})/(icon|background)$")
AUDIO = re.compile(rf"^businesses/({ID})/audios/({AUDIO_NAME})/(active|v[1-9]\d{{0,5}})$")
