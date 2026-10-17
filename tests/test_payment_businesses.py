"""/v1/payment/businesses: Stripe detail views per business, limited to the person's apps and businesses."""

from src.models.admin import Scope
from tests.setup.platform import business
from tests.setup.staff import staff

URL = "/v1/payment/businesses"
VIEWER = [Scope.BUSINESS_VIEW, Scope.INVOICE_VIEW, Scope.USAGE_VIEW]


def test_detail_invoices_and_daily_usage(client, bearer, services):
    biz = business(services, subscribed=True)
    client.post("/v1/usage", json={"callId": "call-abc", "seconds": 61},
                headers=bearer("soneeva-voice", [Scope.HUB_USAGE_WRITE], sub=biz))
    headers = staff(services, bearer)
    (row,) = client.get(URL, headers=headers()).json()["businesses"]
    assert row["business"]["id"] == biz and row["subscription"]["planCode"] == "per-minute"
    detail = client.get(f"{URL}/{biz}", headers=headers()).json()
    assert detail["plan"]["code"] == "per-minute" and detail["subscription"]["active"] is True
    invoices = client.get(f"{URL}/{biz}/invoices", headers=headers()).json()["invoices"]
    assert invoices[0]["number"] == "INV-1" and invoices[0]["amountPaid"] == 120
    usage = client.get(f"{URL}/{biz}/usage", headers=headers()).json()
    assert usage == {"days": [{"day": "2026-10-15", "seconds": 61}]}


def test_people_only_see_their_apps_and_businesses(client, bearer, services):
    biz = business(services)
    outsider = staff(services, bearer, "out", "viewer", VIEWER, apps=["soneeva"], businesses=["other"])
    assert client.get(f"{URL}/{biz}", headers=outsider()).status_code == 404
    assert client.get(URL, headers=outsider()).json()["businesses"] == []
    member = staff(services, bearer, "in", "viewer", VIEWER, apps=["soneeva"])
    assert len(client.get(URL, headers=member()).json()["businesses"]) == 1


def test_each_detail_needs_its_scope(client, bearer, services):
    biz = business(services)
    headers = staff(services, bearer, "b", "biz-only", [Scope.BUSINESS_VIEW], apps=["soneeva"])
    assert client.get(f"{URL}/{biz}", headers=headers()).status_code == 200
    assert client.get(f"{URL}/{biz}/invoices", headers=headers()).status_code == 403
    assert client.get(f"{URL}/{biz}/usage", headers=headers()).status_code == 403
