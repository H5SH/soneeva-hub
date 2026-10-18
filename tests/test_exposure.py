"""Which endpoints answer without credentials. The public list is asserted exhaustively: a new route
that forgets its auth fails `test_every_route_is_public_or_refuses_anonymous_callers` by name."""

import re

import pytest
from fastapi.testclient import TestClient

from main import attach, create_app

#: Reachable with no token, each for a stated reason.
PUBLIC = {
    ("GET", "/health"): "liveness probe",
    ("POST", "/webhooks/stripe/{app_id}"): "Stripe signs the body instead",
    ("GET", "/v1/public/plans"): "the site reads the price list",
    ("GET", "/v1/agent/openapi.json"): "the agent API's schema, so an HTTP-only agent reads real shapes",
    ("GET", "/recover"): "the break-glass sign-in form; every action needs its session",
    ("GET", "/"): "a Soneeva landing page; it only reveals a link for a signed-in /recover session",
    ("GET", "/favicon.ico"): "the Soneeva icon, as on every page",
}


@pytest.fixture
def client(services, monkeypatch):
    """As deployed: ENVIRONMENT=production, so the API docs are not served at all."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    app = create_app()
    attach(app, services)
    return TestClient(app)


def routes(app) -> list[tuple[str, str]]:
    """Every operation. An included router hides its children behind `original_router`, so
    walking `app.routes` alone silently finds almost nothing."""
    found, pending = [], list(app.routes)
    while pending:
        route = pending.pop()
        if (inner := getattr(route, "original_router", None)) is not None:
            pending.extend(inner.routes)
            continue
        found += [(m, route.path) for m in sorted(getattr(route, "methods", None) or [])
                  if m not in ("HEAD", "OPTIONS")]
    return found


def test_every_route_is_public_or_refuses_anonymous_callers(client):
    leaked = []
    for method, path in routes(client.app):
        if (method, path) in PUBLIC:
            continue
        response = client.request(method, re.sub(r"\{[^}]+\}", "x", path))
        if response.status_code not in (401, 403):
            leaked.append((method, path, response.status_code))
    assert not leaked, f"answered an anonymous caller: {leaked}"


def test_the_public_list_names_only_routes_that_exist(client):
    assert set(PUBLIC) <= set(routes(client.app)), set(PUBLIC) - set(routes(client.app))


def test_the_walk_covers_every_operation_in_the_schema(client):
    """Guards the audit itself: a walk that finds nothing would pass vacuously."""
    operations = sum(len([m for m in ops if m in ("get", "post", "put", "patch", "delete")])
                     for ops in client.app.openapi()["paths"].values())
    assert len(routes(client.app)) >= operations > 40


def test_health_names_nothing_and_docs_are_off(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == client.get("/openapi.json").status_code == 404


def test_the_webhook_rejects_an_unsigned_payload(client, services):
    """Public, but authenticated by the Stripe signature: the real gateway's parser refuses it."""
    from src.payment.stripe.gateway import StripeGateway

    services.db.rows[("apps", "someapp")] = {"name": "Some app"}
    client.app.state.webhooks.live._payment = StripeGateway("sk_test_x", "whsec_x")
    forged = client.post("/webhooks/stripe/someapp", json={"id": "evt_forged", "type": "x", "data": {}})
    assert forged.status_code == 400 and "signature" in forged.json()["detail"].lower()


def test_the_agent_api_wants_an_operator_token_not_a_hub_jwt(client, bearer):
    from tests.setup.staff import staff

    agent = [(m, p) for m, p in routes(client.app) if p.startswith("/v1/agent/") and "openapi" not in p]
    for method, path in agent:
        url = re.sub(r"\{[^}]+\}", "x", path)
        assert client.request(method, url).status_code == 401, path
        assert client.request(method, url, headers={"Authorization": "Bearer kat_forged"}).status_code == 401
    jwt_headers = staff(client.app.state, bearer)()  # a valid karbon-admin JWT is not an agent token
    assert client.get("/v1/agent/whoami", headers=jwt_headers).status_code == 401
    schema = client.get("/v1/agent/openapi.json").json()
    assert schema["paths"] and all(p.startswith("/v1/agent/") for p in schema["paths"])
