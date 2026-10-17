"""Names and tags people give a caller (`callers/{e164}` in the business database)."""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.ivr import E164
from src.tunables import tunable

#: q that reads as a phone number: searched as a number prefix, otherwise as a name prefix.
NUMBERISH = re.compile(r"^[+\d][\d\s().-]*$")


def clean_tag(raw: str) -> str:
    """Lowercase, trimmed, inner whitespace as `-`."""
    return re.sub(r"\s+", "-", raw.strip().lower())


def clean_tags(raw: list[str]) -> list[str]:
    """Deduped in order; too many or too long is refused, not cut."""
    tags = list(dict.fromkeys(t for t in (clean_tag(r) for r in raw) if t))
    longest, most = tunable("callers.maxTagLength"), tunable("callers.maxTags")
    if any(len(t) > longest for t in tags):
        raise ValueError(f"A tag is at most {longest} characters")
    if len(tags) > most:
        raise ValueError(f"At most {most} tags")
    return tags


def clean_reason(value: str | None) -> str | None:
    """Why a number is blocked: inner whitespace collapsed, empty is none, too long is refused."""
    value = " ".join((value or "").split()) or None
    if value and len(value) > tunable("callers.maxBlockedReason"):
        raise ValueError(f"A reason is at most {tunable('callers.maxBlockedReason')} characters")
    return value


class CallerPatch(BaseModel):
    """What a person may set on a caller. Leaving a field out keeps it; "" clears the name.
    `blocked` false unblocks (the reason goes too); `blockedReason` only goes with `blocked` true."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    tags: list[str] | None = None
    blocked: bool | None = None
    blockedReason: str | None = None

    @field_validator("blockedReason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        return clean_reason(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if len(value) > tunable("callers.maxName"):
            raise ValueError(f"A name is at most {tunable('callers.maxName')} characters")
        return value

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else clean_tags(value)

    @model_validator(mode="after")
    def _something(self) -> "CallerPatch":
        if self.name is None and self.tags is None and self.blocked is None:
            raise ValueError("Send name, tags or blocked")
        if self.blockedReason and self.blocked is not True:
            raise ValueError("blockedReason goes with blocked: true")
        return self

    @property
    def edits(self) -> bool:
        """Changes the name or tags (`callers.edit`); `blocked` needs `callers.block`."""
        return self.name is not None or self.tags is not None

    def fields(self) -> dict:
        out: dict = {}
        if self.name is not None:
            out |= {"name": self.name, "nameLower": self.name.lower()}
        if self.tags is not None:
            out["tags"] = self.tags
        return out


class CallerBlock(BaseModel):
    """Block a number, also one that never called (its caller doc is created)."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(pattern=E164)
    blocked: bool = True
    blockedReason: str | None = None

    @field_validator("blockedReason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        return clean_reason(value)

    @model_validator(mode="after")
    def _blocks(self) -> "CallerBlock":
        if not self.blocked:
            raise ValueError("POST only blocks; unblock with PATCH")
        return self


def block_fields(blocked: bool, reason: str | None, by: str, now: datetime) -> dict:
    """What blocking writes; unblocking sets `blocked` false and clears the rest (None = delete)."""
    if blocked:
        return {"blocked": True, "blockedReason": reason, "blockedBy": by, "blockedAt": now}
    return {"blocked": False, "blockedReason": None, "blockedBy": None, "blockedAt": None}


class CallerQuery(BaseModel):
    """A page of callers: by number or name prefix (`q`), by one tag, or most recent first."""

    q: str = ""
    tag: str = ""
    blocked: bool = False
    limit: int
    before: str = ""

    @property
    def number(self) -> str | None:
        """`q` as an E.164 prefix when it reads as a number."""
        if not NUMBERISH.match(self.q):
            return None
        digits = re.sub(r"\D", "", self.q)
        digits = digits[2:] if self.q.lstrip().startswith("00") else digits
        return f"+{digits}" if digits else None
