"""The Shopify charge as Shopify sees it, and reading the token the console sealed."""

import base64
import json
import os
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.stores import StoreBillingError
from src.stores.seal import check_key, unseal
from src.stores.shopify import ShopifyBilling


def seal(key: bytes, text: bytes) -> str:
    """As the console's `seal` does: `iv.tag.ciphertext`, each base64url."""
    iv = os.urandom(12)
    secret = AESGCM(key).encrypt(iv, text, None)
    parts = (iv, secret[-16:], secret[:-16])
    return ".".join(base64.urlsafe_b64encode(p).rstrip(b"=").decode() for p in parts)


@pytest.mark.parametrize(("answer", "charged"), [
    ({"data": {"appUsageRecordCreate": {"appUsageRecord": {"id": "gid://1"}, "userErrors": []}}}, True),
    ({"data": {"appUsageRecordCreate": {"appUsageRecord": None,
                                        "userErrors": [{"message": "Failed to create"}]}}}, False),
    ({"errors": [{"message": "Throttled"}]}, False),
])
async def test_a_charge_as_shopify_sees_and_answers_it(monkeypatch, answer, charged):
    key = os.urandom(32)
    seen = []

    def shopify(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer)

    real = httpx.AsyncClient
    mocked = lambda **kw: real(transport=httpx.MockTransport(shopify), **kw)  # noqa: E731
    monkeypatch.setattr(httpx, "AsyncClient", mocked)
    billing = ShopifyBilling(base64.b64encode(key).decode())
    args = ("rosa.myshopify.com", seal(key, b"shpat_x"), "gid://line", Decimal("0.16"), "USD", "95 seconds",
            "call-1")
    if charged:
        await billing.charge(*args)
    else:
        with pytest.raises(StoreBillingError):
            await billing.charge(*args)
    assert str(seen[0].url) == "https://rosa.myshopify.com/admin/api/2026-10/graphql.json"
    assert seen[0].headers["X-Shopify-Access-Token"] == "shpat_x"
    body = json.loads(seen[0].content)
    assert "idempotencyKey: $key" in body["query"] and "@idempotent" not in body["query"]
    assert body["variables"] == {"lineItem": "gid://line", "description": "95 seconds", "key": "call-1",
                                 "price": {"amount": "0.16", "currencyCode": "USD"}}


def test_the_token_the_console_sealed_is_read_back():
    key = os.urandom(32)
    sealed = seal(key, b"shpat_x")
    assert unseal(sealed, base64.b64encode(key).decode()) == "shpat_x"
    with pytest.raises(StoreBillingError):
        unseal(sealed, base64.b64encode(os.urandom(32)).decode())


def test_a_deploy_needs_the_key_the_console_seals_with():
    check_key(base64.b64encode(os.urandom(32)).decode())
    for bad in ("", "not base64!", base64.b64encode(b"short").decode()):
        with pytest.raises(ValueError):
            check_key(bad)
