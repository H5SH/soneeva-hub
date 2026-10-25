"""Gemini on Vertex AI (roles/aiplatform.user on the hub's service account); model and region in settings."""

from google import genai
from google.genai import types
from pydantic import TypeAdapter

from src.reader.base import MenuRow, Reader
from src.tunables import tunable

RULES = (
    "Use only what is in the document: never invent items, prices or facts, and leave out what you "
    "cannot read. Keep prices as plain numbers without currency signs."
)
TEXT = (
    "This is a business document (a menu, price list, flyer or similar) that a phone agent will "
    "answer callers from. Write out everything useful in it as clean plain text: items with prices, "
    "sizes and options, opening hours, addresses, contact details and other facts, grouped under "
    "short headings, without markdown. " + RULES
)
MENU = (
    "This is a menu or price list. Return every item it lists, with its category (the section it is "
    "under), price and a short description when it has one. An item with several sizes is one row "
    "per size, the size in its name. " + RULES
)

ROWS = TypeAdapter(list[MenuRow])


class GeminiReader(Reader):
    def __init__(self, project: str) -> None:
        self._project = project
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        self._client = self._client or genai.Client(
            vertexai=True, project=self._project, location=tunable("knowledge.location"))
        return self._client

    async def _ask(self, data: bytes, kind: str, prompt: str, schema: type | None = None) -> str:
        config = types.GenerateContentConfig(temperature=0)
        if schema:
            config.response_mime_type, config.response_schema = "application/json", schema
        response = await self.client.aio.models.generate_content(
            model=tunable("knowledge.model"),
            contents=[types.Part.from_bytes(data=data, mime_type=kind), prompt], config=config)
        return (response.text or "").strip()

    async def text(self, data: bytes, kind: str) -> str:
        return await self._ask(data, kind, TEXT)

    async def menu(self, data: bytes, kind: str) -> list[MenuRow]:
        return ROWS.validate_json(await self._ask(data, kind, MENU, list[MenuRow]) or "[]")
