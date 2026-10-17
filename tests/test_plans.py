"""Plans are Stripe products: metered plans, the soneeva seed, repricing and the public price list."""

import asyncio

from cli.__main__ import parser
from cli.output import Printer


def invoke(services, *argv) -> None:
    args = parser().parse_args(list(argv))
    asyncio.run(args.handler(services, Printer(False), args))


def product(services, code: str, app: str = "soneeva"):
    return services.payment.live.products[f"prod_{app}_{code}"]


def test_metered_plan_bills_per_started_minute_on_its_meter(services):
    invoke(services, "app:create", "soneeva")
    invoke(services, "plan:save", "soneeva", "per-minute", "--unit", "minute", "--amount", "10")
    plan = product(services, "per-minute")
    assert (plan.amount, plan.unit, plan.meter_id) == (10, "minute", "mtr_soneeva_per-minute")


def test_seed_soneeva_sells_per_minute_and_lists_the_rest_as_soon(services, client):
    invoke(services, "seed:soneeva")
    invoke(services, "seed:soneeva")  # idempotent: nothing new in Stripe
    assert len(services.payment.live.products) == 5 and len(services.payment.live.prices) == 4
    plans = client.get("/v1/public/plans", params={"app": "soneeva"}).json()["plans"]
    assert plans[0] == {"code": "per-minute", "name": "Per minute", "price": 0.1, "unit": "minute",
                        "currency": "usd", "features": [], "status": "active", "icon": None}
    soon = {p["code"]: (p["price"], p["unit"]) for p in plans[1:] if p["status"] == "soon"}
    assert soon == {"base": (10, "hour"), "pro": (20, "hour"), "premium": (1, "minute"),
                    "enterprise": (None, "month")}


def test_hidden_plans_are_not_public(services, client):
    invoke(services, "app:create", "soneeva")
    invoke(services, "plan:save", "soneeva", "old", "--amount", "500", "--status", "hidden")
    assert client.get("/v1/public/plans", params={"app": "soneeva"}).json() == {"plans": []}


def test_status_is_product_metadata_and_keeps_the_price(services):
    invoke(services, "app:create", "soneeva")
    invoke(services, "plan:save", "soneeva", "pro", "--amount", "2000", "--status", "soon")
    invoke(services, "plan:save", "soneeva", "pro", "--amount", "2000")  # unset keeps soon
    assert product(services, "pro").status == "soon"
    plan = asyncio.run(services.plans.live.set_status("soneeva", "pro", "active"))
    assert plan.active and plan.price_id == "price_pro_1" and services.payment.live.prices == ["price_pro_1"]


def test_a_new_amount_is_a_new_default_price_and_the_old_one_is_archived(services):
    invoke(services, "app:create", "soneeva")
    invoke(services, "plan:save", "soneeva", "pro", "--amount", "2000")
    invoke(services, "plan:save", "soneeva", "pro", "--amount", "2500")
    assert product(services, "pro").price_id == "price_pro_2"
    assert services.payment.live.archived == ["price_pro_1"]


def test_plan_delete_archives_the_product_and_price(services):
    invoke(services, "app:create", "soneeva")
    invoke(services, "plan:save", "soneeva", "pro", "--amount", "2000")
    invoke(services, "plan:delete", "soneeva", "pro")
    assert services.payment.live.archived == ["price_pro_1"] and services.payment.live.products == {}


def test_reads_are_cached_and_writes_drop_the_cache(services):
    invoke(services, "app:create", "soneeva")
    invoke(services, "plan:save", "soneeva", "pro", "--amount", "2000")
    before = services.payment.live.calls
    for _ in range(3):
        asyncio.run(services.plans.live.for_app("soneeva"))
    assert services.payment.live.calls == before + 1
    invoke(services, "plan:save", "soneeva", "pro", "--name", "Pro")
    assert asyncio.run(services.plans.live.get("soneeva", "pro")).name == "Pro"


def test_public_plans_needs_no_auth_and_an_unknown_app_is_empty(client):
    assert client.get("/v1/public/plans", params={"app": "nope"}).json() == {"plans": []}


def test_the_code_is_derived_from_the_name_and_an_edit_keeps_the_order(services):
    invoke(services, "app:create", "soneeva")
    for name in ("Basic", "Pro Plus", "Team"):
        invoke(services, "plan:save", "soneeva", "--name", name)
    codes = lambda: [p.code for p in asyncio.run(services.admin.get_app("soneeva")).plans]  # noqa: E731
    assert codes() == ["basic", "pro-plus", "team"]
    invoke(services, "plan:save", "soneeva", "pro-plus", "--name", "Pro Plus+")
    assert codes() == ["basic", "pro-plus", "team"]


def test_seed_grants_app_scopes_and_writes_the_catalogue(services):
    invoke(services, "seed:soneeva")
    voice = services.db.rows[("tools", "soneeva-voice")]
    assert {"hub.usage.write", "hub.plan.view"} <= set(voice["scopes"]) and voice["subjectKeys"]
    assert "hub.business.create" in services.db.rows[("apps", "soneeva")]["scopes"]
    assert "hub.handoff.create" in services.db.rows[("tools", "karbon-admin")]["scopes"]
    assert services.db.rows[("scopes", "soneeva")]["scopes"][0]["id"] == "overview.view"
