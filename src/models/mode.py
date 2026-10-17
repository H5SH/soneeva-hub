"""Stripe modes. `{appId}_staging` is the app itself in test mode: same document and keys, another id."""

from enum import StrEnum

STAGING = "_staging"
STAGING_ONLY = "A staging caller only reaches test businesses"


class Mode(StrEnum):
    LIVE = "live"
    TEST = "test"


def staged(app_id: str) -> tuple[str, Mode]:
    """The app id without `_staging`, and the mode that suffix selects."""
    base = app_id.removesuffix(STAGING)
    return base, Mode.TEST if base != app_id else Mode.LIVE


def staging_id(app_id: str) -> str:
    return app_id + STAGING
