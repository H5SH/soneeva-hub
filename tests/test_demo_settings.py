"""The demo's settings: read with Ivr.View or Demo.Settings, written whole with Demo.Settings."""

import json
from pathlib import Path

from src.models.admin import Scope
from src.models.demo import DemoSettings
from tests.test_plans import invoke

SETTINGS = json.loads((Path(__file__).parent / "data" / "demo_settings.json").read_text())
URL = "/v1/demo/settings"
#: As stored and served: every field, defaults filled in.
STORED = DemoSettings(**SETTINGS).model_dump()


def staff(services, bearer, uid, role):
    services.db.rows[("users", uid)] = {"uid": uid, "role": role}
    return lambda: bearer("karbon-admin", [Scope.HUB_DEMO_VIEW, Scope.HUB_DEMO_UPDATE], uid=uid)


def test_demo_settings_need_their_scopes_and_are_validated(client, bearer, services):
    invoke(services, "seed:soneeva")
    services.db.rows[("roles", "viewer")] = {"scopes": ["Ivr.View"]}
    boss, viewer = staff(services, bearer, "boss", "superadmin"), staff(services, bearer, "vic", "viewer")
    sales = staff(services, bearer, "sam", "sales")
    assert client.get(URL, headers=boss()).status_code == 404  # nothing imported yet
    assert client.put(URL, json=SETTINGS, headers=viewer()).status_code == 403
    assert client.put(URL, json=SETTINGS | {"display": {}}, headers=boss()).status_code == 422
    assert client.put(URL, json=SETTINGS, headers=boss()).json() == STORED
    assert client.get(URL, headers=viewer()).json() == STORED
    assert client.get(URL, headers=sales()).status_code == 403
    assert services.db.rows[("demo", "settings")]["ivr"]["agentMenu"]["default"] == "spark"

