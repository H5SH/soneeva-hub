"""`(default)/audit`: who did what to which business."""

import uuid
from datetime import UTC, datetime
from typing import Any

from src.db import Database
from src.models.db import Default


async def audit(db: Database, action: str, business_id: str, by: str, data: dict[str, Any]) -> None:
    await db.upsert_live(Default.AUDIT, uuid.uuid4().hex, {
        "action": action, "businessId": business_id, "by": by, "data": data,
        "at": datetime.now(UTC),
    })
