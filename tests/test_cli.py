"""The CLI drives the same services as the dashboard. These check the wiring, not Firestore."""

import asyncio

import pytest

from app.models.admin import SUPERADMIN, Scope
from app.models.db import Collection
from app.services.admin import AdminError, AdminService
from app.services.billing import BillingService
from app.services.coupons import CouponService
from app.services.roles import RoleStore
from app.services.tokens.keys import KeyStore
from cli.__main__ import parser
from cli.context import Context
from cli.output import Printer
from tests.conftest import FakeDatabase, FakePayment


class FakeAuth:
    def __init__(self): self.created, self.deleted = [], []

    async def create_user(self, email):
        self.created.append(email)
        return "uid-new", "https://reset.link/abc"

    async def delete_user(self, uid): self.deleted.append(uid)


@pytest.fixture
def ctx():
    db = FakeDatabase()
    db.rows[(Collection.ROLES.value, SUPERADMIN)] = {"scopes": [str(s) for s in Scope]}
    payment = FakePayment()
    roles = RoleStore(db)
    context = Context(
        admin=AdminService(db, KeyStore(db), roles, payment),
        coupons=CouponService(db, payment),
        billing=BillingService(db, payment),
        auth=FakeAuth(),
        settings=None,
    )
    context.db, context.payment = db, payment  # for assertions
    return context


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
    assert ctx.payment.prices == [("snapback", "starter", 2900)]
    assert "price=price_snapback_starter" in out


def test_plan_save_without_an_amount_is_the_free_tier(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    out = invoke(ctx, capsys, "plan:save", "snapback", "free", "--name", "Free")
    assert ctx.payment.prices == []
    assert "free tier" in out


def test_plan_save_is_idempotent_and_does_not_re_create_the_price(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    invoke(ctx, capsys, "plan:save", "snapback", "pro", "--amount", "7500")
    invoke(ctx, capsys, "plan:save", "snapback", "pro", "--amount", "7500", "--name", "Pro")
    assert len(ctx.payment.prices) == 1


def test_coupon_create_resolves_the_apps_products_itself(ctx, capsys):
    invoke(ctx, capsys, "app:create", "snapback")
    invoke(ctx, capsys, "plan:save", "snapback", "pro", "--amount", "7500")
    invoke(ctx, capsys, "coupon:create", "snapback", "--code", "LAUNCH99")
    assert ctx.payment.discounts == [("LAUNCH99", ["prod_snapback_pro"])]


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


def test_user_create_prints_the_reset_link(ctx, capsys):
    out = invoke(ctx, capsys, "user:create", "dev@codinative.com")
    assert "https://reset.link/abc" in out
    assert ctx.auth.created == ["dev@codinative.com"]


def test_json_output_is_parseable(ctx, capsys):
    import json
    invoke(ctx, capsys, "app:create", "snapback")
    out = invoke(ctx, capsys, "--json", "app:show", "snapback")
    assert json.loads(out)["id"] == "snapback"
