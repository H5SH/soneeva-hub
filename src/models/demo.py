"""The browser and phone demo's settings: one document, `(default)/demo/settings`, edited in karbon-admin
(Demo.Settings) and followed live by the demo service, which has no defaults of its own."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DIGIT = r"^[0-9*#]$"
#: The two agents a caller or visitor picks first.
Agent = Literal["spark", "smart"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Models(Strict):
    """Smart: speech-to-text, `llm`, text-to-speech. Spark: one speech-to-speech `live` model."""

    llm: str = Field(min_length=1)
    sttLanguage: str = Field(min_length=1)
    ttsVoice: str = Field(min_length=1)
    live: str = Field(min_length=1)
    liveVoice: str = Field(min_length=1)


class Branch(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    callerPrefixes: list[str]


class AgentChoice(Strict):
    id: Agent
    label: str = Field(min_length=1)
    description: str = ""


class AgentDigit(Strict):
    digit: str = Field(pattern=DIGIT)
    agent: Agent


class AgentMenu(Strict):
    """Heard first on the phone; no key within the timeout picks `default`."""

    prompt: str = Field(min_length=1)
    options: list[AgentDigit] = Field(min_length=1)
    default: Agent


class MenuOption(Strict):
    """A demo (`mode`) or an `action` (only `support`, which says `say` and hangs up)."""

    digit: str = Field(pattern=DIGIT)
    label: str = Field(min_length=1)
    mode: str | None = None
    action: Literal["support"] | None = None
    say: str = ""


class Ivr(Strict):
    agentMenu: AgentMenu
    greeting: str = Field(min_length=1)
    invalid: str = Field(min_length=1)
    goodbye: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    timeout: int = Field(ge=1, le=60)
    maxAttempts: int = Field(ge=1, le=10)
    options: list[MenuOption] = Field(min_length=1)


class DemoOption(Strict):
    mode: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""


class Display(Strict):
    transcript: bool
    userTranscript: bool
    agentTranscript: bool
    agentActions: bool
    typing: bool


class Home(Strict):
    loginUrl: str
    siteUrl: str
    plansUrl: str
    accessUrl: str


class DemoSettings(Strict):
    models: Models
    agents: list[AgentChoice] = Field(min_length=1)
    branches: list[Branch]
    ivr: Ivr
    agentOptions: list[DemoOption] = Field(min_length=1)
    display: Display
    home: Home
