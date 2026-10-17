"""Each provider SDK is imported by its own resource folder only, so swapping one touches one folder."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: Imported module prefix → the only folder allowed to import it.
OWNERS = {
    "google.cloud.firestore": "src/db",
    "firebase_admin.firestore": "src/db",
    "google.cloud.storage": "src/storage",
    "firebase_admin.storage": "src/storage",
    "stripe": "src/payment",
    "firebase_admin.auth": "src/auth",
}


def imported(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found |= {node.module} | {f"{node.module}.{a.name}" for a in node.names}
    return found


def offences(rel: str, source: str) -> list[str]:
    bad = [m for m in imported(source) for prefix, owner in OWNERS.items()
           if (m == prefix or m.startswith(prefix + ".") or m.startswith(prefix + "_"))
           and not rel.startswith(owner + "/")]
    if "identitytoolkit" in source and not rel.startswith("src/auth/"):
        bad.append("identitytoolkit")
    return bad


def test_no_provider_sdk_outside_its_resource():
    files = [p for d in ("src", "cli", "scripts") for p in (ROOT / d).rglob("*.py")]
    files += [ROOT / "main.py", ROOT / "app.py"]
    bad = {rel: o for p in files if (o := offences(rel := str(p.relative_to(ROOT)), p.read_text()))}
    assert not bad, bad


def test_the_guard_catches_what_it_should():
    assert offences("src/services/x.py", "from google.cloud.firestore_v1 import Query")
    assert offences("src/services/x.py", "from firebase_admin import firestore_async")
    assert offences("src/services/x.py", "from firebase_admin import storage")
    assert offences("src/routes/x.py", "import stripe")
    assert offences("src/services/x.py", "from firebase_admin import auth")
    assert offences("src/services/x.py", 'URL = "https://identitytoolkit.googleapis.com/v1"')
    assert not offences("src/db/firestore.py", "from google.cloud.firestore_admin_v1 import types")
    assert not offences("src/payment/stripe/gateway.py", "import stripe")
    assert not offences("src/services/x.py", "from src.payment.stripe import StripeGateway")
