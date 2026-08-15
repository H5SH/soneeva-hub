from enum import StrEnum

DELETED_FLAG = "is_deleted"
DELETED_AT = "deleted_at"
CREATED_AT = "created_at"
UPDATED_AT = "updated_at"


class Collection(StrEnum):
    """Every collection this service touches."""

    CUSTOMERS = "customers"
    SUBSCRIPTIONS = "subscriptions"
    EVENTS = "events"
