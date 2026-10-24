"""The phone demo's settings: read with Ivr.View (or any tab's scope), saved tab by tab with that tab's
scope, and reverted to the version before the last save."""

import copy
import json
from pathlib import Path

from src.models.admin import Scope
from src.models.demo import DemoSettings
from tests.test_plans import invoke

SETTINGS = json.loads((Path(__file__).parent / "data" / "demo_settings.json").read_text())
STORED = DemoSettings(**SETTINGS).model_dump()
URL = "/v1/demo/settings"


def staff(services, bearer, uid, scopes):
    services.db.rows[("roles", uid)] = {"scopes": scopes}
    services.db.rows[("users", uid)] = {"uid": uid, "role": uid}
    return lambda: bearer("karbon-admin", [Scope.HUB_DEMO_VIEW, Scope.HUB_DEMO_UPDATE], uid=uid)


def seeded(services):
    invoke(services, "seed:soneeva")
    services.db.rows[("demo", "settings")] = copy.deepcopy(STORED)


def test_each_tab_needs_its_own_scope_and_reading_needs_any(client, bearer, services):
    seeded(services)
    viewer = staff(services, bearer, "viewer", ["Ivr.View"])
    sales = staff(services, bearer, "sales2", ["Demo.Number.Write"])
    other = staff(services, bearer, "other", ["Businesses.View"])
    assert client.get(URL, headers=viewer()).json()["settings"] == STORED
    assert list(client.get(URL, headers=sales()).json()["settings"]) == ["demoNumber"]  # its own tab
    assert client.get(URL, headers=other()).status_code == 403
    demo = STORED["demoNumber"] | {"mode": "ivr"}
    assert client.put(f"{URL}/demoNumber", json=demo, headers=viewer()).status_code == 403
    other_tab = client.put(f"{URL}/soneevaNumber", json=STORED["soneevaNumber"], headers=sales())
    assert other_tab.status_code == 403
    saved = client.put(f"{URL}/demoNumber", json=demo, headers=sales()).json()
    assert saved["settings"]["demoNumber"]["mode"] == "ivr" and saved["saved"]["demoNumber"]["by"] == "sales2"
    assert services.db.rows[("demo", "settings")]["soneevaNumber"] == STORED["soneevaNumber"]  # untouched


def test_a_line_is_checked_whole(client, bearer, services):
    seeded(services)
    sales = staff(services, bearer, "sales2", ["Demo.Number.Write"])
    broken = copy.deepcopy(STORED["demoNumber"])
    broken["steps"]["main"]["options"][0]["next"] = "nowhere"
    assert client.put(f"{URL}/demoNumber", json=broken, headers=sales()).status_code == 422
    twice = copy.deepcopy(STORED["demoNumber"])
    twice["steps"]["main"]["options"][1]["digit"] = twice["steps"]["main"]["options"][0]["digit"]
    assert client.put(f"{URL}/demoNumber", json=twice, headers=sales()).status_code == 422
    assert client.put(f"{URL}/demoNumber", json=STORED["demoNumber"] | {"start": "x"},
                      headers=sales()).status_code == 422


def test_a_save_can_be_reverted(client, bearer, services):
    seeded(services)
    boss = staff(services, bearer, "boss2", ["*"])
    assert client.post(f"{URL}/page/revert", headers=boss()).status_code == 409  # nothing saved yet
    page = STORED["page"] | {"title": "New title"}
    assert client.put(f"{URL}/page", json=page, headers=boss()).json()["saved"]["page"]["revertible"]
    back = client.post(f"{URL}/page/revert", headers=boss()).json()
    assert back["settings"]["page"]["title"] == STORED["page"]["title"]
    assert services.db.rows[("demo", "history")]["page"]["previous"]["title"] == "New title"


def test_nothing_in_the_database_is_a_404(client, bearer, services):
    invoke(services, "seed:soneeva")
    viewer = staff(services, bearer, "viewer", ["Ivr.View"])
    assert client.get(URL, headers=viewer()).status_code == 404


def test_a_transfer_rings_a_real_number_and_falls_back_to_a_step(client, bearer, services):
    seeded(services)
    sales = staff(services, bearer, "sales2", ["Demo.Number.Write"])
    line = copy.deepcopy(STORED["demoNumber"])
    line["steps"]["team"] = {"kind": "transfer", "name": "Our team", "say": "Connecting you.",
                             "phone": "+15551234567", "fallback": "support"}
    line["steps"]["main"]["options"].append({"digit": "9", "label": "Our team", "next": "team"})
    saved = client.put(f"{URL}/demoNumber", json=line, headers=sales()).json()
    assert saved["settings"]["demoNumber"]["steps"]["team"]["ringSeconds"] == 20
    line["steps"]["team"]["fallback"] = "nowhere"
    assert client.put(f"{URL}/demoNumber", json=line, headers=sales()).status_code == 422
    line["steps"]["team"] |= {"fallback": None, "phone": "0612345678"}
    assert client.put(f"{URL}/demoNumber", json=line, headers=sales()).status_code == 422
