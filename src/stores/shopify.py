"""Shopify usage charges, with the token the console keeps in the business's `connections/shopify`."""

from decimal import Decimal

import httpx

from src.stores.base import StoreBilling, StoreBillingError
from src.stores.seal import unseal
from src.tunables import tunable

TIMEOUT_SECONDS = 10.0
CHARGE = """
mutation Charge($lineItem: ID!, $price: MoneyInput!, $description: String!, $key: String!) {
  appUsageRecordCreate(subscriptionLineItemId: $lineItem, price: $price, description: $description,
                       idempotencyKey: $key) {
    appUsageRecord { id }
    userErrors { message }
  }
}
"""


class ShopifyBilling(StoreBilling):
    def __init__(self, connections_key: str) -> None:
        self._key = connections_key

    async def charge(self, store: str, sealed_token: str, line_item: str, amount: Decimal, currency: str,
                     description: str, key: str) -> None:
        version = tunable("stores.shopifyApiVersion")
        variables = {"lineItem": line_item, "description": description, "key": key,
                     "price": {"amount": str(amount), "currencyCode": currency}}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"https://{store}/admin/api/{version}/graphql.json",
                    headers={"X-Shopify-Access-Token": unseal(sealed_token, self._key)},
                    json={"query": CHARGE, "variables": variables})
            response.raise_for_status()
            answer = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise StoreBillingError(f"Shopify: {error}") from error
        result = (answer.get("data") or {}).get("appUsageRecordCreate") or {}
        problems = answer.get("errors") or result.get("userErrors")
        if problems or not result.get("appUsageRecord"):
            raise StoreBillingError(f"Shopify: {problems or 'no usage record'}")
