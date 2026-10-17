"""Decisions from settings.json. Explained in SETTINGS.md."""

import json
from functools import lru_cache
from typing import Any

from src.utils import get_path


@lru_cache
def tunables() -> dict[str, Any]:
    return json.loads(get_path("settings.json").read_text())


def tunable(path: str) -> Any:
    """Dotted lookup, e.g. tunable("voice.language")."""
    node: Any = tunables()
    for part in path.split("."):
        node = node[part]
    return node
