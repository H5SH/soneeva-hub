"""Tags and search, plans created from a name, and coupon discount terms."""

from tests.setup.platform import business
from tests.setup.staff import staff

APPS = "/v1/payment/apps"


def test_tags_are_normalised_and_searched_with_name_and_id(client, bearer, services):
    headers = staff(services, bearer)
    client.post(APPS, json={"id": "snapback", "name": "Snapback", "tags": ["Acme", " acme", "EU West"]},
                headers=headers())
    client.post(APPS, json={"id": "other", "name": "Other"}, headers=headers())
    assert client.get(f"{APPS}/snapback", headers=headers()).json()["tags"] == ["acme", "eu-west"]
    found = lambda q: [a["id"] for a in client.get(APPS, params={"q": q}, headers=headers()).json()["apps"]]  # noqa: E731
    assert found("ACME") == ["snapback"] and found("snap") == ["snapback"] and found("Other") == ["other"]
    patched = client.patch(f"{APPS}/other", json={"tags": ["Beta"]}, headers=headers()).json()
    assert patched["tags"] == ["beta"]


def test_a_plan_posted_without_a_code_gets_one_from_its_name(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    made = client.post(f"{APPS}/soneeva/plans", json={"name": "Pro Plus", "amount": 2999}, headers=headers())
    assert made.status_code == 201 and made.json()["code"] == "pro-plus"
    again = client.post(f"{APPS}/soneeva/plans", json={"name": "Pro Plus"}, headers=headers())
    assert again.status_code == 409


def test_coupon_terms_reach_stripe_and_come_back(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    body = {"code": "SPRING2026", "kind": "amount", "value": 1000, "currency": "eur",
            "duration": "repeating", "months": 3, "maxRedemptions": 50, "expiresAt": "2026-12-31T00:00:00Z"}
    made = client.post(f"{APPS}/soneeva/coupons", json=body, headers=headers())
    assert made.status_code == 201
    assert made.json()["discount"] == {"kind": "amount", "value": 1000, "currency": "eur",
                                       "duration": "repeating", "months": 3,
                                       "expiresAt": "2026-12-31T00:00:00+00:00"}
    terms = services.payment.live.coupons["SPRING2026"].discount
    assert terms.max_redemptions == 50 and terms.months == 3
    listed = client.get(f"{APPS}/soneeva/coupons", headers=headers()).json()["coupons"][0]
    assert listed["discount"]["kind"] == "amount" and listed["maxRedemptions"] == 50


def test_incoherent_terms_are_refused_before_stripe(client, bearer, services):
    business(services)
    headers = staff(services, bearer)
    for body, reason in [
        ({"kind": "percent", "value": 120}, "more than 100"),
        ({"duration": "repeating"}, "number of months"),
        ({"duration": "once", "months": 2}, "Months only"),
        ({"kind": "amount", "value": 9.5}, "must be whole"),
        ({"value": 0}, "take something off"),
    ]:
        refused = client.post(f"{APPS}/soneeva/coupons", json=body, headers=headers())
        assert refused.status_code == 400 and reason in refused.json()["detail"], body
    assert services.payment.live.coupons == {}


def test_a_coupon_without_terms_is_a_free_code(client, bearer, services):
    business(services)
    made = client.post(f"{APPS}/soneeva/coupons", json={}, headers=staff(services, bearer)()).json()
    assert made["discount"]["kind"] == "percent" and made["discount"]["value"] == 100
    assert made["discount"]["duration"] == "forever"
