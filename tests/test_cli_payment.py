"""Payment commands: `--json` prints what the matching route returns, not display strings."""

import asyncio
import json

import pytest

from cli.__main__ import parser
from cli.output import Printer
from src.services.errors import AdminError


def run(ctx, capsys, *argv) -> str:
    args = parser().parse_args(list(argv))
    asyncio.run(args.handler(ctx, Printer(args.json), args))
    return capsys.readouterr().out


def as_json(ctx, capsys, *argv):
    return json.loads(run(ctx, capsys, "--json", *argv))


def test_app_json_matches_the_route_and_tags_are_searchable(services, capsys):
    made = as_json(services, capsys, "app:create", "snapback", "--name", "Snapback", "--tags", "Acme,EU")
    assert made["tags"] == ["acme", "eu"] and made["hasWebhookSecret"] is False and made["keys"] == []
    as_json(services, capsys, "app:create", "other")
    assert [a["id"] for a in as_json(services, capsys, "apps", "--q", "acme")["apps"]] == ["snapback"]
    key = as_json(services, capsys, "key:rotate", "snapback")
    assert key["appId"] == "snapback" and key["privateKey"].startswith("-----BEGIN")


def test_the_webhook_secret_is_write_only(services, capsys):
    run(services, capsys, "app:create", "snapback")
    out = run(services, capsys, "--json", "app:update", "snapback", "--webhook-secret", "whsec_cli")
    assert json.loads(out)["hasWebhookSecret"] is True and "whsec_cli" not in out
    assert services.db.rows[("apps", "snapback")]["stripeWebhookSecret"] == "whsec_cli"


def test_webhook_create_refuses_a_local_base_url(services, capsys):
    run(services, capsys, "app:create", "snapback")
    services.settings = type("S", (), {"service_base_url": "http://localhost:8080"})()
    with pytest.raises(AdminError, match="local address"):
        run(services, capsys, "webhook:create", "snapback")
    services.settings = type("S", (), {"service_base_url": "https://hub.example"})()
    made = as_json(services, capsys, "webhook:create", "snapback")
    assert made == {"endpointId": "we_1", "url": "https://hub.example/webhooks/stripe/snapback",
                    "hasWebhookSecret": True}


def test_plans_and_status_as_json(services, capsys):
    run(services, capsys, "app:create", "snapback")
    plan = as_json(services, capsys, "plan:save", "snapback", "--name", "Pro Plus", "--amount", "2900")
    assert plan["code"] == "pro-plus" and plan["amount"] == 2900 and plan["priceId"]
    soon = as_json(services, capsys, "plan:status", "snapback", "pro-plus", "soon")
    assert soon["status"] == "soon"
    assert [p["code"] for p in as_json(services, capsys, "plans", "snapback")["plans"]] == ["pro-plus"]


def test_coupon_terms_from_the_command_line(services, capsys):
    run(services, capsys, "app:create", "snapback")
    run(services, capsys, "plan:save", "snapback", "pro", "--amount", "7500")
    made = as_json(services, capsys, "coupon:create", "snapback", "--code", "HALFOFF3", "--percent", "50",
                   "--duration", "repeating", "--months", "3", "--max", "10")
    assert made["discount"]["value"] == 50 and made["discount"]["months"] == 3
    assert made["maxRedemptions"] == 10
    assert "50% off, for 3 months" in run(services, capsys, "coupons", "snapback")
    with pytest.raises(AdminError, match="number of months"):
        run(services, capsys, "coupon:create", "snapback", "--duration", "repeating")
