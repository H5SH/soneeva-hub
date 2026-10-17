"""Per-number call menus (`numbers/{e164}`) and recorded messages (`audios/{name}`), per the contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

E164 = r"^\+[1-9]\d{6,14}$"
#: A recording's name: also a storage folder and part of the voice's audio URL.
SLUG = r"^[a-z0-9][a-z0-9-]{0,39}$"


class Human(BaseModel):
    """Ring a person; when nobody answers within `ringSeconds`, the fallback takes the call."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(pattern=E164)
    ringSeconds: int = Field(default=20, ge=5, le=60)
    fallback: Literal["agent", "audio"] = "agent"
    fallbackAudio: str | None = Field(default=None, pattern=SLUG)

    @model_validator(mode="after")
    def _fallback_audio(self) -> "Human":
        if self.fallback == "audio" and not self.fallbackAudio:
            raise ValueError("A message fallback names its message (fallbackAudio)")
        if self.fallback == "agent":
            self.fallbackAudio = None
        return self


class IvrOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    digit: str = Field(pattern=r"^[1-9]$")
    action: Literal["agent", "audio", "human"]
    branchId: str | None = Field(default=None, max_length=128)
    audio: str | None = Field(default=None, pattern=SLUG)
    human: Human | None = None

    @model_validator(mode="after")
    def _target(self) -> "IvrOption":
        if self.action == "audio" and not self.audio:
            raise ValueError(f"Key {self.digit} plays a message: name it (audio)")
        if self.action == "human" and not self.human:
            raise ValueError(f"Key {self.digit} rings a person: give human.phone")
        keep = {"agent": "branchId", "audio": "audio", "human": "human"}[self.action]
        for field in ("branchId", "audio", "human"):
            if field != keep:
                setattr(self, field, None)
        return self


class NumberConfig(BaseModel):
    """`direct`: greeting, then the agent. Otherwise the greeting states `options` and one key is read."""

    model_config = ConfigDict(extra="forbid")

    direct: bool = True
    greeting: str = Field(default="", pattern=r"^([a-z0-9][a-z0-9-]{0,39})?$")
    options: list[IvrOption] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def _menu(self) -> "NumberConfig":
        digits = [o.digit for o in self.options]
        if len(set(digits)) != len(digits):
            raise ValueError("Each key is used once")
        if not self.direct and not self.options:
            raise ValueError("A menu needs at least one option")
        return self

    def audios(self) -> set[str]:
        """Every recording this number plays."""
        names = {self.greeting} if self.greeting else set()
        for option in self.options:
            names |= {option.audio} if option.audio else set()
            if option.human and option.human.fallbackAudio:
                names.add(option.human.fallbackAudio)
        return names


class AudioPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    #: Blank: the business's call language.
    language: str = Field(default="", pattern=r"^([a-z]{2,3}(-[A-Za-z0-9]{2,8})*)?$")
