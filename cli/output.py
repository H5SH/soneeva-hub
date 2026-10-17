"""Printing. Plain columns by default, JSON with --json so output can be piped."""

import json
from collections.abc import Callable
from typing import Any


class Printer:
    def __init__(self, as_json: bool = False) -> None:
        self.as_json = as_json

    def emit(self, payload: Any, human: Callable[[], None]) -> None:
        """`--json` prints `payload` — the body the matching route returns — and plain output runs
        `human`. Display strings never reach JSON, so `| jq` works."""
        if self.as_json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            human()

    def table(self, rows: list[dict[str, Any]], columns: list[str]) -> None:
        if self.as_json:
            print(json.dumps(rows, indent=2, default=str))
            return
        if not rows:
            print("  (none)")
            return
        widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
        print("  " + "  ".join(c.upper().ljust(widths[c]) for c in columns))
        for row in rows:
            print("  " + "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))

    def detail(self, data: dict[str, Any]) -> None:
        if self.as_json:
            print(json.dumps(data, indent=2, default=str))
            return
        width = max((len(k) for k in data), default=0)
        for key, value in data.items():
            if isinstance(value, list) and any(isinstance(v, dict) for v in value):
                # A list of records earns a line each; a list of words reads better joined.
                rows = ["  ".join(f"{k}={v}" for k, v in row.items() if v not in (None, "", [], {}))
                        for row in value]
                print(f"  {key.ljust(width)}  {rows[0]}")
                for row in rows[1:]:
                    print(f"  {' ' * width}  {row}")
                continue
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "—"
            print(f"  {key.ljust(width)}  {value if value not in ('', None) else '—'}")

    def done(self, message: str) -> None:
        if self.as_json:
            print(json.dumps({"ok": True, "message": message}))
        else:
            print(f"  {message}")

    def block(self, text: str) -> None:
        print(text if self.as_json else "\n" + text + "\n")
