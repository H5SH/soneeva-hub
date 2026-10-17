"""src/models/db.py and src/models/paths.py are the only places that spell a collection or a bucket path."""

import ast
import re
from pathlib import Path

from src.models import paths
from src.models.db import camel, to_doc

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = {ROOT / "src/models/db.py", ROOT / "src/models/paths.py"}
#: Calls that take a collection or document name first.
NAMED = {"collection", "document", "collection_group", "get_rows", "get_row", "create_row", "set_row",
         "delete_row", "claim", "path"}
BUCKET = re.compile(r"^(users|apps|businesses|cache|audio|advertisements)/")


def _docstrings(tree: ast.AST) -> set[int]:
    return {id(node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}


def _literal_start(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        return str(first.value) if isinstance(first, ast.Constant) else ""
    return None


def offences(source: str) -> list[str]:
    tree = ast.parse(source)
    docs, found = _docstrings(tree), []
    for node in ast.walk(tree):
        func = node.func if isinstance(node, ast.Call) else None
        name = getattr(func, "attr", getattr(func, "id", None))
        if name in NAMED and node.args and _literal_start(node.args[0]) is not None:  # type: ignore[attr-defined]
            found.append(f"line {node.lineno}: {name}() with a literal")
        text = _literal_start(node)
        if text is not None and id(node) not in docs and BUCKET.match(text):
            found.append(f"line {node.lineno}: bucket path {text!r}")
    return found


def test_nothing_outside_the_registry_spells_a_collection_or_a_path():
    files = [p for d in ("src", "cli", "scripts") for p in (ROOT / d).rglob("*.py") if p not in REGISTRY]
    bad = {str(p.relative_to(ROOT)): o for p in files if (o := offences(p.read_text()))}
    assert not bad, bad


def test_the_guard_catches_what_it_should():
    assert offences('db.collection("callers")') and offences('x.document(f"{a}")')
    assert offences('path("callers", p)') and offences('db.get_row("apps", x)')
    assert offences('p = f"businesses/{b}/avatar"') and offences('p = "cache/fillers/en/x.ulaw"')
    assert not offences('db.collection(Default.CALLERS)') and not offences('url = f"/v1/businesses/{b}"')
    assert not offences('def f():\n    """businesses/{id}/avatar is documented here."""')


def test_paths_and_their_patterns_agree():
    assert paths.USER_AVATAR.match(paths.user_avatar("u1"))
    assert paths.APP_AVATAR.match(paths.app_avatar("soneeva"))
    assert paths.BUSINESS_AVATAR.match(paths.business_avatar("b1"))
    assert paths.BRANCH_AVATAR.match(paths.branch_avatar("b1", "main"))
    assert paths.AUDIO.match(paths.audio("b1", "opening"))
    assert paths.AUDIO.match(paths.audio("b1", "opening", 2))
    greeting = paths.greeting_cache("b1", "nl", "order", "ab12", "ulaw")
    assert greeting == "businesses/b1/cache/greeting/nl/order-ab12.ulaw"
    assert paths.filler_cache("nl", "ab12") == "cache/fillers/nl/ab12.ulaw"
    assert paths.AD_IMAGE.match(paths.advertisement_image("ad1", "icon"))
    assert paths.AD_IMAGE.match(paths.advertisement_image("ad1", "background"))


def test_documents_are_written_in_camel_case():
    assert camel("allowed_return_hosts") == "allowedReturnHosts" and camel("dbId") == "dbId"
    doc = to_doc({"app_id": "a", "keys": [{"public_key": "k"}]})
    assert doc == {"appId": "a", "keys": [{"publicKey": "k"}]}
