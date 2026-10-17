import json

from src.models.admin import Scope
from src.services.catalogue import seed_hub
from src.services.people.rules import PROFILE_WRITE
from src.utils import get_path


def test_scopes_json_describes_every_scope_in_every_language():
    described = json.loads(get_path("scopes.json").read_text())
    for language in ("en", "nl", "de"):
        assert set(described[language]) == {str(s) for s in Scope} | {PROFILE_WRITE}
        assert all(v["label"] and v["description"] for v in described[language].values())


async def test_seed_hub_writes_the_catalogue_and_default_roles(services):
    await seed_hub(services.db)
    catalogue = services.db.rows[("scopes", "karbon-hub")]["scopes"]
    assert {"id": "payment.admin", "group": "admin"} in catalogue
    assert "payment.app.view" in services.db.rows[("roles", "sales")]["scopes"]
