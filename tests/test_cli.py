"""The CLI drives the same services as the API. These check the wiring, not Firestore."""

import asyncio

import pytest

from cli.__main__ import parser
from cli.output import Printer
from src.models.admin import SUPERADMIN, Scope
from src.models.db import Default
from src.services.errors import AdminError


@pytest.fixture
def ctx(services):
    services.db.rows[(Default.ROLES.value, SUPERADMIN)] = {"scopes": [str(s) for s in Scope]}
    return services


def invoke(ctx, capsys, *argv) -> str:
    args = parser().parse_args(list(argv))
    asyncio.run(args.handler(ctx, Printer(args.json), args))
    return capsys.readouterr().out


def test_app_create_then_show(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback", "--name", "Snapback",
           "--return-url", "https://x.ngrok.app/plans", "--hosts", "x.ngrok.app, localhost")
    out = invoke(ctx, capsys, "app:show", "snapback")
    assert "Snapback" in out
    assert "x.ngrok.app, localhost" in out


def test_app_create_rejects_a_duplicate(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    with pytest.raises(AdminError):
        invoke(ctx, capsys, "app:create", "snapback")


def test_plan_save_creates_a_stripe_price_for_a_paid_plan(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    out = invoke(ctx, capsys, "plan:save", "snapback", "starter",
                 "--name", "Starter", "--amount", "2900", "--trial", "7")
    assert ctx.payment.live.prices == ["price_starter_1"]
    assert ctx.payment.live.products["prod_snapback_starter"].trial_days == 7
    assert "price=price_starter_1" in out


def test_plan_save_without_an_amount_is_the_free_tier(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    out = invoke(ctx, capsys, "plan:save", "snapback", "free", "--name", "Free")
    assert ctx.payment.live.prices == [] and "prod_snapback_free" in ctx.payment.live.products
    assert "no price" in out


def test_plan_save_is_idempotent_and_does_not_re_create_the_price(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    invoke(ctx, capsys, "plan:save", "snapback", "pro", "--amount", "7500")
    invoke(ctx, capsys, "plan:save", "snapback", "pro", "--amount", "7500", "--name", "Pro")
    invoke(ctx, capsys, "plan:save", "snapback", "pro", "--name", "Pro")  # no amount keeps the price
    assert ctx.payment.live.prices == ["price_pro_1"] and ctx.payment.live.archived == []


def test_coupon_create_resolves_the_apps_products_itself(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    invoke(ctx, capsys, "plan:save", "snapback", "pro", "--amount", "7500")
    invoke(ctx, capsys, "coupon:create", "snapback", "--code", "LAUNCH99")
    assert ctx.payment.live.coupon_products == {"LAUNCH99": ["prod_snapback_pro"]}


def test_coupon_create_refuses_when_there_is_nothing_to_discount(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    with pytest.raises(AdminError, match="paid plan"):
        invoke(ctx, capsys, "coupon:create", "snapback")


def test_key_rotate_prints_a_pasteable_env_block(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    out = invoke(ctx, capsys, "key:rotate", "snapback")
    assert "PAYMENT_APPLICATION_ID=snapback" in out
    assert "PAYMENT_KEY_ID=" in out
    assert "PAYMENT_PRIVATE_KEY=" in out
    assert "\\n" in out  # escaped, so it survives a single-line variable


def test_role_save_rejects_an_unknown_scope(ctx, capsys):
    with pytest.raises(SystemExit, match="Unknown scope"):
        invoke(ctx, capsys, "role:save", "auditor", "--scopes", "App.View,Nope.Invent")


def test_role_save_all_expands_to_every_scope(ctx, capsys):
    invoke(ctx, capsys, "role:save", "owner", "--scopes", "all")
    out = invoke(ctx, capsys, "role:show", "owner")
    assert str(Scope.KEY_ROTATE) in out


def test_person_create_prints_the_reset_link(ctx, capsys):
    out = invoke(ctx, capsys, "person:create", "dev@soneeva.com")
    assert "https://reset.example/dev@soneeva.com" in out
    assert any(a.email == "dev@soneeva.com" for a in ctx.accounts.accounts.values())


def test_json_output_is_parseable(ctx, capsys):
    import json
    invoke(ctx, capsys, "app:create", "snapback")
    out = invoke(ctx, capsys, "--json", "app:show", "snapback")
    assert json.loads(out)["id"] == "snapback"


def test_person_save_gives_an_existing_account_its_first_record(ctx, capsys):
    ctx.accounts.add("boss", email="boss@soneeva.com")
    invoke(ctx, capsys, "person:save", "boss", "--role", SUPERADMIN)
    assert ctx.db.rows[(Default.USERS.value, "boss")]["role"] == SUPERADMIN
    with pytest.raises(AdminError, match="No such person"):
        invoke(ctx, capsys, "person:save", "ghost", "--role", SUPERADMIN)


def test_db_prune_lists_then_deletes_only_unused_collections(ctx, capsys):
    ctx.db.rows[("resources", "roles")] = {"superadmin": ["*"]}
    ctx.db.rows[(Default.ROLES.value, "sales")] = {"scopes": []}
    assert "resources" in invoke(ctx, capsys, "db:prune")
    assert ("resources", "roles") in ctx.db.rows
    invoke(ctx, capsys, "db:prune", "--apply")
    assert ("resources", "roles") not in ctx.db.rows and (Default.ROLES.value, "sales") in ctx.db.rows
