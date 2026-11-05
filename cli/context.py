"""Service wiring for the command line: the same `Services` main.py builds.

No scope checks: running this needs GOOGLE_APPLICATION_CREDENTIALS, which already grants full
Firestore access. Scopes constrain people acting through the API.
"""

from dotenv import load_dotenv

load_dotenv()

from src.config import get_settings  # noqa: E402
from src.models.admin import SUPERADMIN, Scope  # noqa: E402
from src.models.identity import Identity  # noqa: E402
from src.wiring import Services, build  # noqa: E402

OPERATOR = Identity(uid="cli", email="cli@localhost", role=SUPERADMIN, payment_scopes=list(Scope))

Context = Services


def build_context() -> Services:
    return build(get_settings())
