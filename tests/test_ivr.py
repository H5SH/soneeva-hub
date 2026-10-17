"""Per-number call menus and recorded messages: who may read and change them, and what changes do."""

import asyncio
import json
from pathlib import Path

import pytest

from src.models.admin import Scope
from tests.setup.platform import business

FIXTURE = json.loads((Path(__file__).parent / "contract" / "hub.json").read_text())
APP = [Scope.HUB_BUSINESS_VIEW, Scope.HUB_VOICE_UPDATE, Scope.HUB_MEDIA_VIEW, Scope.HUB_MEDIA_UPDATE]
LINE, OTHER = "+31201112222", "+31203334444"
WAV = b"RIFF0000WAVEfmt "


@pytest.fixture
def biz(services):
    """A live business with two numbers; owner1 (owner, every scope), clerk (ivr.view), temp (nothing)."""
    biz = business(services, subscribed=True)
    row = services.db.rows[("businesses", biz)]
    row |= {"voiceUrl": "https://voice.example", "numbers": [{"phoneNumber": LINE}, {"phoneNumber": OTHER}]}
    bdb = services.business_dbs[f"b-{biz}"]
    bdb.rows[("roles", "clerk")] = {"scopes": ["overview.view", "ivr.view"]}
    bdb.rows[("roles", "temp")] = {"scopes": ["overview.view", "numbers.view"]}
    for uid in ("clerk", "temp"):
        bdb.rows[("users", uid)] = {"role": uid}
    services.db.rows[("users", "boss")] = {"role": "superadmin"}
    services.db.rows[("users", "seller")] = {"role": "sales"}
    return biz


@pytest.fixture
def call(client, bearer):
    def run(method: str, path: str, uid: str, sub: str = "-", **kwargs):
        return client.request(method, path, headers=bearer("console", APP, sub=sub, uid=uid), **kwargs)

    return run


def menu(**over) -> dict:
    options = [{"digit": "1", "action": "agent", "branchId": "main"},
               {"digit": "2", "action": "audio", "audio": "opening"},
               {"digit": "3", "action": "human", "human": {"phone": "+31612345678"}}]
    return {"direct": False, "greeting": "opening", "options": options} | over


def test_a_recording_is_saved_pending_archived_on_change_and_restarts_the_voice(biz, call, services):
    url = f"/v1/businesses/{biz}/audios/opening"
    saved = call("PUT", url, "owner1", biz, json={"text": "Welcome."})
    assert saved.status_code == 200, saved.text
    first = saved.json()
    assert (first["status"], first["version"], first["restarting"]) == ("pending", 1, True)
    deployed = len(services.voice._deployer.deployed)
    assert call("PUT", url, "owner1", biz, json={"text": "Welcome."}).json()["restarting"] is False
    assert len(services.voice._deployer.deployed) == deployed  # nothing changed, nothing restarts

    asyncio.run(services.storage.put(f"businesses/{biz}/audios/opening/active", WAV, "audio/wav"))
    again = call("PUT", url, "owner1", biz, json={"text": "Hello again.", "language": "nl"}).json()
    assert again["version"] == 2 and again["versions"] == [again["versions"][0]]
    assert (again["versions"][0]["n"], again["versions"][0]["text"]) == (1, "Welcome.")
    assert services.storage.files[f"businesses/{biz}/audios/opening/v1"].data == WAV
    for bad in ({"text": ""}, {"text": "x" * 1001}, {"text": "Hi", "language": "Dutch!"}):
        assert call("PUT", url, "owner1", biz, json=bad).status_code == 422
    bad_name = f"/v1/businesses/{biz}/audios/Bad_Name"
    assert call("PUT", bad_name, "owner1", biz, json={"text": "x"}).status_code == 422


def test_a_number_menu_is_checked_before_it_is_saved(biz, call, services):
    url = f"/v1/businesses/{biz}/ivr/numbers/%2B31201112222"
    assert call("PUT", url, "owner1", biz, json=menu()).status_code == 422  # no recording yet
    call("PUT", f"/v1/businesses/{biz}/audios/opening", "owner1", biz, json={"text": "Press 1."})
    human = lambda phone: menu(options=[{"digit": "3", "action": "human", "human": {"phone": phone}}])  # noqa: E731
    for bad, status in ((human(OTHER), 422), (human("0612345678"), 422),
                        (menu(options=[{"digit": "1", "action": "agent"}] * 2), 422),
                        (menu(options=[]), 422), (menu(options=[{"digit": "0", "action": "agent"}]), 422),
                        (menu(options=[{"digit": "1", "action": "agent", "branchId": "nope"}]), 422)):
        assert call("PUT", url, "owner1", biz, json=bad).status_code == status, bad
    assert call("PUT", f"/v1/businesses/{biz}/ivr/numbers/%2B31209999999", "owner1", biz,
                json=menu()).status_code == 404
    saved = call("PUT", url, "owner1", biz, json=menu())
    assert saved.status_code == 200 and saved.json()["restarting"] is True
    assert saved.json()["config"]["options"][2]["human"]["ringSeconds"] == 20
    listed = call("GET", f"/v1/businesses/{biz}/ivr/numbers", "clerk", biz).json()["numbers"]
    assert [(n["phoneNumber"], n["configured"]) for n in listed] == [(LINE, True), (OTHER, False)]
    assert listed[1]["config"] == {"direct": True, "greeting": "", "options": []}
    assert call("GET", f"/v1/businesses/{biz}/audios/opening", "clerk", biz).json()["usedBy"] == [LINE]


def test_scopes_decide_who_reads_and_who_changes(biz, call):
    numbers, audio = f"/v1/businesses/{biz}/ivr/numbers", f"/v1/businesses/{biz}/audios/opening"
    assert call("GET", numbers, "temp", biz).status_code == 403
    assert call("GET", f"/v1/businesses/{biz}/audios", "temp", biz).status_code == 403
    assert call("GET", numbers, "clerk", biz).status_code == 200
    assert call("PUT", audio, "clerk", biz, json={"text": "x"}).status_code == 403
    assert call("PUT", f"{numbers}/%2B31201112222", "clerk", biz, json=menu(direct=True)).status_code == 403
    assert call("GET", numbers, "seller").status_code == 200  # platform Businesses.View reads
    assert call("PUT", audio, "boss", json={"text": "x"}).status_code == 403  # but never writes
    assert call("GET", numbers, "owner1", "otherbiz").status_code == 404


def test_recordings_in_use_are_kept_and_old_versions_go(biz, call, services):
    base = f"/v1/businesses/{biz}/audios/opening"
    call("PUT", base, "owner1", biz, json={"text": "One."})
    asyncio.run(services.storage.put(f"businesses/{biz}/audios/opening/active", WAV, "audio/wav"))
    call("PUT", base, "owner1", biz, json={"text": "Two."})
    call("PUT", f"/v1/businesses/{biz}/ivr/numbers/%2B31201112222", "owner1", biz, json=menu(direct=True))
    media = f"/v1/media/businesses/{biz}/audios/opening"
    assert call("GET", f"{media}/v1", "clerk", biz).content == WAV
    assert call("GET", f"{media}/v1", "temp", biz).status_code == 403
    assert call("PUT", f"{media}/active", "owner1", biz, content=WAV).status_code == 405
    assert call("DELETE", f"{media}/active", "owner1", biz).status_code == 409
    assert call("DELETE", base, "owner1", biz).status_code == 409
    assert call("DELETE", f"{media}/v1", "clerk", biz).status_code == 403
    assert call("DELETE", f"{media}/v1", "owner1", biz).json() == {"deleted": True}
    assert f"businesses/{biz}/audios/opening/v1" not in services.storage.files
    assert call("DELETE", f"{base}/versions/1", "owner1", biz).status_code == 404
    call("PUT", f"/v1/businesses/{biz}/ivr/numbers/%2B31201112222", "owner1", biz, json={"direct": True})
    assert call("DELETE", base, "owner1", biz).json()["deleted"] is True
    assert call("GET", base, "owner1", biz).status_code == 404


def test_shapes_match_the_contract(biz, call, services):
    from tests.test_contract import same_keys

    for name in ("opening", "complaints-menu", "closed"):  # each with one earlier version
        call("PUT", f"/v1/businesses/{biz}/audios/{name}", "owner1", biz, json={"text": name})
        asyncio.run(services.storage.put(f"businesses/{biz}/audios/{name}/active", WAV, "audio/wav"))
        call("PUT", f"/v1/businesses/{biz}/audios/{name}", "owner1", biz, json=FIXTURE["audioPut"])
    line = f"/v1/businesses/{biz}/ivr/numbers/%2B31201112222"
    put = call("PUT", line, "owner1", biz, json=FIXTURE["ivrNumberPut"])
    same_keys(put.json(), FIXTURE["ivrNumberSaved"])
    assert put.json()["config"] == FIXTURE["ivrNumberPut"]
    same_keys(call("GET", f"/v1/businesses/{biz}/ivr/numbers", "owner1", biz).json(), FIXTURE["ivrNumbers"])
    same_keys(call("GET", f"/v1/businesses/{biz}/audios", "owner1", biz).json(), FIXTURE["audios"])
    saved = call("PUT", f"/v1/businesses/{biz}/audios/opening", "owner1", biz, json={"text": "New."})
    same_keys(saved.json(), FIXTURE["audioSaved"])
    call("PUT", f"/v1/businesses/{biz}/ivr/numbers/%2B31201112222", "owner1", biz, json={"direct": True})
    deleted = call("DELETE", f"/v1/businesses/{biz}/audios/closed", "owner1", biz)
    same_keys(deleted.json(), FIXTURE["audioDeleted"])
