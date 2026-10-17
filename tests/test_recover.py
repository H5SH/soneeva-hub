"""/recover: break-glass rotation of hand-rotated tools, with a fake Identity Toolkit and fake database."""

import re
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from main import attach, create_app
from src.routes import recover

SECRET = "test-secret-" * 4
PEOPLE = {"boss": "superadmin", "sales1": "sales", "gone": "superadmin"}


class FakeIdentity:
    """Password `pw` for every `{uid}@x.com`."""

    async def uid(self, email: str, password: str) -> str | None:
        return email.split("@")[0] if password == "pw" else None


@pytest.fixture
def client(services):
    recover.SIGN_INS._seen.clear()
    services.settings = SimpleNamespace(auth_secret=SECRET, environment="test")
    services.identity = FakeIdentity()
    for uid, role in PEOPLE.items():
        services.db.rows[("users", uid)] = {"uid": uid, "email": f"{uid}@x.com", "role": role,
                                           "disabled": uid == "gone"}
    services.db.rows[("roles", "superadmin")] = {"scopes": ["*"]}
    services.db.rows[("apps", "softbox")] = {"name": "softbox", "status": "active", "keys": []}
    services.db.rows[("tools", "karbon-admin")] = {"name": "Karbon admin", "status": "active", "keys": []}
    services.db.rows[("tools", "soneeva-voice")] = {"name": "Voice", "keys": [], "subjectKeys": True}
    app = create_app()
    attach(app, services)
    return TestClient(app, base_url="https://testserver")


def sign_in(client, uid: str, password: str = "pw"):
    return client.post("/recover/sign-in", data={"email": f"{uid}@x.com", "password": password},
                       follow_redirects=False)


def csrf(client) -> str:
    return re.search(r'name="csrf" value="(\w+)"', client.get("/recover").text).group(1)


def test_unconfigured_says_so_and_refuses(services):
    app = create_app()
    attach(app, services)
    client = TestClient(app)
    assert "Not configured" in client.get("/recover").text
    assert sign_in(client, "boss").status_code == 403


@pytest.mark.parametrize("uid,password", [("boss", "wrong"), ("sales1", "pw"), ("gone", "pw"),
                                          ("stranger", "pw")])
def test_wrong_password_no_role_disabled_or_unknown_get_one_refusal(client, uid, password):
    response = sign_in(client, uid, password)
    assert response.status_code == 401 and "no access" in response.text
    assert "recover" not in response.cookies


def test_sign_in_is_rate_limited_per_ip_and_email(client):
    assert [sign_in(client, "boss", "wrong").status_code for _ in range(6)] == [401] * 5 + [429]
    assert sign_in(client, "sales1", "wrong").status_code == 401


def test_the_cookie_is_short_signed_and_locked_down(client):
    header = sign_in(client, "boss").headers["set-cookie"]
    for part in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=900"):
        assert part.lower() in header.lower()


@pytest.mark.parametrize("cookie", [
    f"boss.{int(time.time()) + 600}.{'0' * 64}",
    f"boss.{int(time.time()) - 1}.{recover._sign(SECRET, f'boss.{int(time.time()) - 1}')}",
    f"sales1.{int(time.time()) + 600}.{recover._sign(SECRET, f'sales1.{int(time.time()) + 600}')}",
])
def test_forged_expired_or_roleless_cookies_are_signed_out(client, services, cookie):
    client.cookies.set("recover", cookie, path="/")
    assert "Sign in with" in client.get("/recover").text
    assert client.post("/recover/rotate", data={"tool": "karbon-admin", "csrf": "x"}).status_code == 401
    assert services.db.rows[("tools", "karbon-admin")]["keys"] == []


def test_rotation_needs_the_csrf_token(client, services):
    sign_in(client, "boss")
    assert client.post("/recover/rotate", data={"tool": "karbon-admin"}).status_code == 403
    assert client.post("/recover/rotate", data={"tool": "karbon-admin", "csrf": "0" * 64}).status_code == 403
    assert services.db.rows[("tools", "karbon-admin")]["keys"] == []


def test_only_hand_rotated_tools_are_listed_and_rotated(client, services):
    sign_in(client, "boss")
    page = client.get("/recover").text
    assert 'value="karbon-admin"' in page and 'value="softbox"' not in page
    assert 'value="soneeva-voice"' not in page and "redeployed" in page
    for other in ("softbox", "soneeva-voice"):
        refused = client.post("/recover/rotate", data={"tool": other, "csrf": csrf(client)})
        assert refused.status_code == 400
    response = client.post("/recover/rotate", data={"tool": "karbon-admin", "csrf": csrf(client)})
    assert response.status_code == 200 and "Shown once" in response.text
    assert "HUB_APP_ID=karbon-admin" in response.text and "BEGIN PRIVATE KEY" in response.text
    assert response.headers["cache-control"] == "no-store"
    kid = services.db.rows[("tools", "karbon-admin")]["keys"][-1]["kid"]
    assert kid in response.text
    [entry] = [row for (table, _), row in services.db.rows.items() if table == "audit"]
    assert entry["action"] == "key.rotate" and entry["by"] == "boss"
    assert entry["data"] == {"toolId": "karbon-admin", "kid": kid, "via": "recover"}


def test_sign_out_ends_the_session(client):
    sign_in(client, "boss")
    assert client.post("/recover/sign-out", data={"csrf": csrf(client)},
                       follow_redirects=False).status_code == 303
    assert "Sign in with" in client.get("/recover").text


def test_home_links_signed_in_staff_to_recovery_and_everyone_else_to_the_site(client):
    page = client.get("/").text
    assert "Visit soneeva.com" in page and "/recover" not in page
    sign_in(client, "boss")
    page = client.get("/").text
    assert "Open key recovery" in page and "soneeva.com" not in page
