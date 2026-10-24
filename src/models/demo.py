"""The phone demo's settings: one document, `(default)/demo/settings`, edited in karbon-admin tab by tab
and followed live by the demo service, which has no defaults of its own.

Two phone lines, each answered `direct`ly by one agent or through an `ivr` of steps: a menu (keys to
other steps), an agent (Spark or Smart with its own prompt, and `knowledge` served to it as the menu
resource), a message (said, then back to the menu or goodbye) or a transfer (to a real phone number,
with a `fallback` step when nobody answers). Menus may lead to menus, to any depth.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIGIT = r"^[0-9*#]$"
E164 = r"^\+[1-9]\d{6,14}$"
#: The two agents: Spark speaks natively (Gemini Live), Smart is speech-to-text, LLM, text-to-speech.
Agent = Literal["spark", "smart"]
#: Each tab saves one section; who may is in SECTION_SCOPES.
Section = Literal["soneevaNumber", "demoNumber", "page", "config"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentStep(Strict):
    kind: Literal["agent"] = "agent"
    #: Shown with the call in karbon-admin.
    name: str = Field(min_length=1, max_length=60)
    agent: Agent
    #: Who the agent is and what it does; the shared voice and safety rules are added by the demo.
    prompt: str = Field(min_length=1, max_length=20000)
    #: Served to the agent as its menu resource: a menu, prices, opening hours, facts.
    knowledge: str = Field(default="", max_length=50000)


class Choice(Strict):
    digit: str = Field(pattern=DIGIT)
    label: str = Field(min_length=1, max_length=60)
    #: A step id of the same line.
    next: str = Field(min_length=1)


class MenuStep(Strict):
    kind: Literal["menu"] = "menu"
    name: str = Field(min_length=1, max_length=60)
    say: str = Field(min_length=1, max_length=2000)
    options: list[Choice] = Field(min_length=1)
    #: Where no key within the timeout goes; none: the menu is said again.
    default: str | None = None


class MessageStep(Strict):
    kind: Literal["message"] = "message"
    name: str = Field(min_length=1, max_length=60)
    say: str = Field(min_length=1, max_length=2000)
    then: Literal["back", "hangup"]


class TransferStep(Strict):
    kind: Literal["transfer"] = "transfer"
    name: str = Field(min_length=1, max_length=60)
    #: Said before ringing, e.g. "Connecting you to our team"; may be empty.
    say: str = Field(default="", max_length=2000)
    phone: str = Field(pattern=E164)
    ringSeconds: int = Field(default=20, ge=5, le=60)
    #: Where a call nobody answers goes (a step id); none: goodbye.
    fallback: str | None = None


Step = Annotated[AgentStep | MenuStep | MessageStep | TransferStep, Field(discriminator="kind")]


class Line(Strict):
    """One phone number: answered by `direct`, or by the `ivr` from `start`."""

    phone: str = Field(pattern=E164)
    mode: Literal["direct", "ivr"]
    direct: AgentStep
    start: str = Field(min_length=1)
    steps: dict[str, Step] = Field(min_length=1)

    @model_validator(mode="after")
    def _linked(self) -> "Line":
        ids = set(self.steps)
        if self.start not in ids:
            raise ValueError(f"start '{self.start}' is not a step")
        for sid, step in self.steps.items():
            targets = ([o.next for o in step.options] + ([step.default] if step.default else [])
                       if isinstance(step, MenuStep) else
                       [step.fallback] if isinstance(step, TransferStep) and step.fallback else [])
            missing = sorted(set(targets) - ids)
            if missing:
                raise ValueError(f"step '{sid}' leads to unknown steps: {', '.join(missing)}")
            if isinstance(step, MenuStep):
                digits = [o.digit for o in step.options]
                if len(digits) != len(set(digits)):
                    raise ValueError(f"step '{sid}' uses a key twice")
        return self


class Page(Strict):
    """The demo's one-screen info page."""

    title: str = Field(min_length=1, max_length=120)
    intro: str = Field(max_length=600)
    capabilities: list[str] = Field(max_length=8)
    siteUrl: str


class Models(Strict):
    llm: str = Field(min_length=1)
    sttLanguage: str = Field(min_length=1)
    ttsVoice: str = Field(min_length=1)
    live: str = Field(min_length=1)
    liveVoice: str = Field(min_length=1)


class Branch(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    callerPrefixes: list[str]


class Config(Strict):
    models: Models
    #: Twilio's voice for menus and messages.
    menuVoice: str = Field(min_length=1)
    timeout: int = Field(ge=1, le=60)
    maxAttempts: int = Field(ge=1, le=10)
    invalid: str = Field(min_length=1)
    goodbye: str = Field(min_length=1)
    branches: list[Branch]


class DemoSettings(Strict):
    soneevaNumber: Line
    demoNumber: Line
    page: Page
    config: Config


#: Who may save each section (Ivr.View, or any of these, reads them all).
SECTION_SCOPES: dict[str, str] = {
    "soneevaNumber": "Soneeva.Number.Write", "demoNumber": "Demo.Number.Write",
    "page": "Demo.Page.Write", "config": "Demo.Config.Write",
}
SECTION_MODELS: dict[str, type[BaseModel]] = {
    "soneevaNumber": Line, "demoNumber": Line, "page": Page, "config": Config,
}
