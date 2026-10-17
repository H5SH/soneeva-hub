"""get_rows: a doc without `isDeleted` is live; a flagged one is not; limit counts live rows only."""

from src.db import Doc
from src.db.base import live_rows


def _docs(rows: dict[str, dict]) -> list[Doc]:
    return [Doc(doc_id, data) for doc_id, data in rows.items()]


async def test_a_missing_flag_is_live_and_a_flagged_doc_is_not():
    rows = {"a": {"role": "sales"}, "b": {"role": "x", "isDeleted": True}, "c": {"isDeleted": False}}
    assert [r["id"] for r in live_rows(_docs(rows))] == ["a", "c"]


async def test_limit_counts_only_live_rows():
    rows = {"a": {"isDeleted": True}, "b": {}, "c": {}}
    assert [r["id"] for r in live_rows(_docs(rows), limit=1)] == ["b"]
