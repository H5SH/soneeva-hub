"""Where this service runs: the Google Cloud project from its credentials, the region from settings."""

from functools import lru_cache

import google.auth
from google.auth.exceptions import DefaultCredentialsError

from src.tunables import tunable


@lru_cache
def project_id() -> str:
    """The credentials file locally, the metadata server on Cloud Run. Blank when neither exists."""
    try:
        return google.auth.default()[1] or ""
    except DefaultCredentialsError:
        return ""


def region() -> str:
    return tunable("cloud.region")
