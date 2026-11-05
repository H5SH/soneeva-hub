from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_path(*parts: str | Path) -> Path:
    """Resolve a file or folder relative to the project root."""
    return PROJECT_ROOT.joinpath(*parts)


def slug(value: str) -> str:
    """A url- and id-safe code from a human name: "Pro Plus" -> "pro-plus"."""
    cleaned = "".join(c if c.isalnum() else "-" for c in value.strip().lower())
    return "-".join(part for part in cleaned.split("-") if part)


def iso(value: Any) -> Any:
    """A stored time as text; anything else as it is."""
    return value.isoformat() if isinstance(value, datetime) else value
