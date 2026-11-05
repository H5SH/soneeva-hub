"""The store facts both businesses.py and stores.py need: who holds a store, what its plan means."""

from datetime import UTC, datetime
from typing import Any

from src.db import Database, Transaction
from src.models.db import DELETED_AT, DELETED_FLAG, UPDATED_AT, Default
from src.models.platform import Business, BusinessStatus
from src.models.stores import STORE_PLAN_STATUS, StoreRef
from src.models.subscription import SubscriptionRecord


async def claim_store(db: Database, store: StoreRef, business_id: str, test: bool) -> bool:
    """One store, one business; False when another holds it. In a transaction, as a disconnected store's
    soft-deleted row is still there to take over."""
    doc = store.doc(test)

    async def run(tx: Transaction) -> bool:
        row = await tx.get(Default.STORES, doc)
        if row and not row.get(DELETED_FLAG):
            return row.get("businessId") == business_id
        now = datetime.now(UTC)
        tx.set(Default.STORES, doc, {"businessId": business_id, "platform": store.platform.value,
                                     "storeId": store.storeId, "connectedAt": now,
                                     DELETED_FLAG: False, DELETED_AT: None, UPDATED_AT: now})
        return True

    return await db.transaction(run)


def store_record(business: Business) -> SubscriptionRecord | None:
    """Its subscription, when it pays through its store."""
    plan = business.storePlan
    if not plan:
        return None
    return SubscriptionRecord(app_id=business.appId, tenant=business.id, plan_code=plan.code,
                              status=STORE_PLAN_STATUS[plan.status], subscription_id=plan.subscriptionId)


def uninstalled_after(before: Business, status: str) -> dict[str, Any]:
    """`uninstalled` once the platform sets `status`. Deleting on the platform keeps the date for the
    retention job but gives up what a reinstall would restore; any other status ends the uninstall."""
    if status != BusinessStatus.DELETED:
        return {"uninstalled": None}
    if not before.uninstalled:
        return {}
    return {"uninstalled": {"status": None, "at": before.uninstalled.at}}
