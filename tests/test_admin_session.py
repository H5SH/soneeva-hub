"""The admin dashboard's Auth.js session on the hub: tokens made by @auth/core (tests/data), dummy secret."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from main import attach, create_app
from src.routes import recover

TOKENS = json.loads((Path(__file__).parent / "data" / "authjs_tokens.json").read_text())
NAME = "authjs.session-token"
HEADER, KEY, IV, CIPHER, TAG = TOKENS["boss"].split(".")
TAMPERED = ".".join([HEADER, KEY, IV, CIPHER[:10] + ("A" if CIPHER[10] != "A" else "B") + CIPHER[11:], TAG])


@pytest.fixture
def client(services):
    services.settings = SimpleNamespace(auth_secret=TOKENS["secret"], environment="test")
    services.identity = None
    for uid, role in {"boss": "superadmin", "sales1": "sales"}.items():
        services.db.rows[("users", uid)] = {"uid": uid, "email": f"{uid}@x.com", "role": role}
    services.db.rows[("roles", "superadmin")] = {"scopes": ["*"]}
    services.db.rows[("tools", "karbon-admin")] = {"name": "Karbon admin", "status": "active", "keys": []}
    app = create_app()
    attach(app, services)
    return TestClient(app, base_url="https://testserver")


def staff(client, cookies: dict[str, str]) -> bool:
    client.cookies.clear()
    for name, value in cookies.items():
        client.cookies.set(name, value, path="/")
    home, page = client.get("/").text, client.get("/recover").text
    assert ("Open key recovery" in home) == ('value="karbon-admin"' in page)
    return "Open key recovery" in home


def test_an_admin_session_with_tools_write_is_staff_on_home_and_recover(client):
    assert not staff(client, {})
    assert staff(client, {NAME: TOKENS["boss"]})
    assert staff(client, {f"__Secure-{NAME}": TOKENS["boss_secure"]})
    value = TOKENS["boss"]
    assert staff(client, {f"{NAME}.0": value[:900], f"{NAME}.1": value[900:]})
    assert "signed in through the admin dashboard" in client.get("/recover").text


@pytest.mark.parametrize("cookies", [
    {NAME: TOKENS["sales1"]},  # no Tools.Write
    {NAME: TOKENS["expired"]},
    {NAME: TOKENS["no_uid"]},
    {NAME: TAMPERED},
    {f"__Secure-{NAME}": TOKENS["boss"]},  # salted with the other cookie name
    {NAME: "not.a.jwe.at.all"},
])
def test_other_admin_sessions_are_not_staff(client, cookies):
    assert not staff(client, cookies)


def test_the_wrong_secret_is_refused(client, services):
    services.settings.auth_secret = "another-secret"
    assert not staff(client, {NAME: TOKENS["boss"]})


def test_an_admin_session_rotates_with_csrf_and_sign_out_leaves_it(client, services):
    client.cookies.set(NAME, TOKENS["boss"], path="/")
    token = re.search(r'name="csrf" value="(\w+)"', client.get("/recover").text).group(1)
    assert client.post("/recover/rotate", data={"tool": "karbon-admin", "csrf": "0" * 64}).status_code == 403
    response = client.post("/recover/rotate", data={"tool": "karbon-admin", "csrf": token})
    assert response.status_code == 200 and "Shown once" in response.text
    client.post("/recover/sign-out", data={"csrf": token})
    assert NAME in client.cookies and "Open key recovery" in client.get("/").text
    assert recover.COOKIE not in client.cookies
