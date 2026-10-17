"""Fails when any tracked .py or .html file is longer than the limit."""

import sys
from pathlib import Path

LIMIT = 300
SUFFIXES = {".py", ".html"}
SKIP = {".venv", ".git", "node_modules", "__pycache__", ".deploy-local"}
ROOT = Path(__file__).resolve().parent.parent


def too_long(root: Path = ROOT, limit: int = LIMIT) -> list[tuple[Path, int]]:
    found = []
    for path in sorted(root.rglob("*")):
        if path.suffix in SUFFIXES and not SKIP & set(path.relative_to(root).parts):
            count = len(path.read_text(encoding="utf-8").splitlines())
            if count > limit:
                found.append((path.relative_to(root), count))
    return found


if __name__ == "__main__":
    offenders = too_long()
    for path, count in offenders:
        print(f"{path}: {count} lines (max {LIMIT})")
    sys.exit(1 if offenders else 0)
