"""Printing. Plain columns by default, JSON with --json so output can be piped."""

import json
from typing import Any


class Printer:
    def __init__(self, as_json: bool = False) -> None:
        self.as_json = as_json

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
