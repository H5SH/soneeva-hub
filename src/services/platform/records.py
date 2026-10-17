"""Demo-owned: the browser demo's snake_case call and message docs as the console's camelCase records.

The only place the hub reads snake_case; business databases are camelCase throughout."""

from datetime import UTC, datetime
from typing import Any


def iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    when = value if value.tzinfo else value.replace(tzinfo=UTC)
    return when.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value: Any, fallback: float = 0) -> float:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else fallback


def _num_or_none(value: Any) -> float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _str(value: Any, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def usage(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    keys = {"inputTokens": "input_tokens", "outputTokens": "output_tokens",
            "thinkingTokens": "thinking_tokens", "cachedTokens": "cached_tokens", "requests": "requests",
            "sttSeconds": "stt_seconds", "ttsCharacters": "tts_characters"}
    return {out: _num(value.get(raw)) for out, raw in keys.items()}


#: camelCase part -> the demo's field. `carrier` is the call's telephony (minutes and confirmations).
COST_PARTS = {"llm": "llm", "stt": "stt", "tts": "tts", "telephony": "carrier", "total": "total"}


def cost(value: Any) -> dict[str, float | None] | None:
    if not isinstance(value, dict):
        return None
    return {out: _num_or_none(value.get(raw)) for out, raw in COST_PARTS.items()}


def to_call(call_id: str, caller: str, d: dict[str, Any]) -> dict[str, Any]:
    models = d.get("models") if isinstance(d.get("models"), dict) else {}
    languages = d.get("languages")
    return {
        "id": call_id,
        "uid": _str(d.get("caller_id")) or caller,
        "agent": _str(d.get("mode"), "—"),
        "status": _str(d.get("status"), "in_progress"),
        "startedAt": iso(d.get("started_at")),
        "endedAt": iso(d.get("ended_at")),
        "durationSeconds": _num_or_none(d.get("duration_seconds")),
        "messageCount": _num(d.get("message_count")),
        "turns": _num(d.get("turns")),
        "languages": [str(x) for x in languages] if isinstance(languages, list) else [],
        "tools": d.get("tools") if isinstance(d.get("tools"), dict) else {},
        "interruptions": _num(d.get("interruptions")),
        "holdingPhrases": _num(d.get("holding_phrases")),
        "endReason": _str(d.get("end_reason")) or None,
        "models": {k: _str(models.get(k)) for k in ("llm", "stt", "tts")},
        "usage": usage(d.get("usage")),
        "cost": cost(d.get("cost")),
    }


def to_message(message_id: str, d: dict[str, Any]) -> dict[str, Any]:
    raw = d.get("tools")
    tools = [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else []
    phrases = d.get("holding_phrases")
    return {
        "id": message_id,
        "index": _num(d.get("index")),
        "role": _str(d.get("role")),
        "content": _str(d.get("content")),
        "language": _str(d.get("language")) or None,
        "createdAt": iso(d.get("created_at")),
        "usage": usage(d.get("usage")),
        "cost": cost(d.get("cost")),
        "responseMs": _num_or_none(d.get("response_ms")),
        "tools": [{
            "name": _str(t.get("name")),
            "arguments": t.get("arguments") if isinstance(t.get("arguments"), dict) else {},
            "result": _str(t.get("result")), "ok": t.get("ok") is not False,
            "durationMs": _num_or_none(t.get("duration_ms")),
        } for t in tools],
        "holdingPhrases": [str(p) for p in phrases] if isinstance(phrases, list) else [],
        "interrupted": d.get("interrupted") is True,
        "error": d.get("error") is True,
        "endedCall": d.get("ended_call") is True,
    }
