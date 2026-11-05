"""What the console sends for a store (tests/contract/hub.json)."""

import json
from pathlib import Path

from src.models.admin import Scope
from tests.test_businesses import CONSOLE

FIXTURE = json.loads((Path(__file__).parents[1] / "contract" / "hub.json").read_text())
STORE = FIXTURE["storeRef"]
SIGNUP = FIXTURE["storeSignup"]
PLAN = FIXTURE["storePlan"]
SCOPES = [*CONSOLE, Scope.HUB_STORE_SIGN_IN, Scope.HUB_STORE_UPDATE, Scope.HUB_BILLING_VIEW,
          Scope.HUB_BILLING_UPDATE, Scope.HUB_NUMBER_VIEW, Scope.HUB_PLAN_VIEW]
STORE_PATH = "/v1/stores/shopify/rosa.myshopify.com"
