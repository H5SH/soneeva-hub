"""A seeded soneeva business, optionally on the per-minute plan."""

import asyncio
import json
from datetime import UTC, datetime

from src.models.advertisement import AdFields
from src.models.platform import BusinessCreate, Caller
from tests.setup.people import people
from tests.test_plans import invoke

#: A database of its own, as every business had before regions: tests read it as `business_dbs["b-{id}"]`.
SIGNUP = BusinessCreate(ownerUid="owner1", name="Pizza Roma", type="restaurant", country="NL",
                        timezone="Europe/Amsterdam", languages=["nl"], dataLocation="europe-west3")


def business(services, subscribed: bool = False) -> str:
    invoke(services, "seed:soneeva")
    created = asyncio.run(services.businesses.create(SIGNUP, "test"))
    asyncio.run(services.businesses.provisioner.run(created.id))
    if subscribed:
        subscribe(services, created.id)
    return created.id


def subscribe(services, business_id: str, sub_id: str = "sub_1"):
    """What a completed checkout of the per-minute plan leaves in Stripe."""
    plan = asyncio.run(services.plans.live.get("soneeva", "per-minute"))
    return services.payment.live.subscribe("soneeva", business_id, plan, sub_id)


def checkout_event(business_id: str, sub_id: str = "sub_1") -> bytes:
    return json.dumps({"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "subscription": sub_id,
        "metadata": {"app": "soneeva", "tenant": business_id},
    }}}).encode()


def serviceperson(services, uid: str = "sp-demo-1", name: str = "Sanne de Vries",
                  phone: str = "+31600000001") -> None:
    """A person whose role is `serviceperson`, with a contact number."""
    account = services.accounts.add(uid, email=f"{uid}@example.com")
    services.accounts.accounts[uid] = account.model_copy(update={"displayName": name})
    services.db.rows[("users", uid)] = {"role": "serviceperson", "phone": phone}


def home_data(services) -> str:
    """For /v1/platform/home: people, two businesses (one assigned to sp-demo-1, whose role is
    Businesses.Access only, the other unassigned), a subscription, an ad published to both, one demo
    call by u1 (whom sp looks after). Returns the assigned business."""
    people(services)
    serviceperson(services)
    rows, accounts = services.db.rows, services.accounts.accounts
    mine = business(services, subscribed=True)
    rows[("roles", "serviceperson")] = {"scopes": ["Businesses.Access", "Profile.Write"]}
    rows[("businesses", mine)] |= {"servicepersonUid": "sp-demo-1", "status": "running"}
    rows[("businesses", "other")] = rows[("businesses", mine)] | {
        "id": "other", "name": "Other Place", "servicepersonUid": None, "status": "pending"}
    accounts["u1"] = accounts["u1"].model_copy(update={"createdAt": "2026-10-01T00:00:00Z",
                                                       "lastSignInAt": "2026-10-02T00:00:00Z"})
    asyncio.run(services.businesses.checklist.set_state(mine, "assigned", True))
    ads = services.advertisements
    ad = asyncio.run(ads.create(AdFields(title="Summer"), "admin"))
    asyncio.run(ads.publish(Caller(app_id="karbon-admin", sub="-"), ad["id"], [mine, "other"]))
    caller = "+31600000001"
    rows[(f"callers/{caller}/calls", "c0")] = {"caller_id": caller, "started_at": datetime.now(UTC),
                                               "duration_seconds": 120}
    return mine
