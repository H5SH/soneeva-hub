"""/v1/businesses/{id}/callers: names, tags and blocks people give the numbers that call a business.

A business token acts for its person, who needs `callers.view` (or `.edit`) to read, `callers.edit`
to name and tag, `callers.block` to block or unblock; a person limited to branches blocks only
callers of those branches. A platform person needs Businesses.View to read, Businesses.Write to change.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Path, Query

from src.dependencies import BusinessDep, CallersDep
from src.models.admin import Scope
from src.models.callers import CallerBlock, CallerPatch, CallerQuery, block_fields, clean_tag
from src.models.db import BusinessDb
from src.models.ivr import E164
from src.models.platform import Business, Caller
from src.services.businesses import BusinessService
from src.services.errors import PlatformError
from src.services.people.access import business_scopes
from src.services.people.rules import CANNOT_READ, holds, reads_business
from src.services.tokens.caller import requires_caller
from src.tunables import tunable

router = APIRouter(prefix="/v1/businesses/{business_id}/callers", tags=["callers"])
READ, EDIT, BLOCK = {"callers.view", "callers.edit"}, {"callers.edit"}, {"callers.block"}


async def allowed(businesses: BusinessService, caller: Caller, business_id: str,
                  *needs: set[str]) -> Business:
    """Each of `needs` (READ, EDIT, BLOCK) met by a business person; a platform person reads by
    `reads_business` and writes with Businesses.Write."""
    business = await businesses.require(caller, business_id)
    if caller.business_id:
        held = await business_scopes(businesses.business_db(business), caller.uid) if caller.uid else []
        for need in needs:
            if not need & set(held):
                raise PlatformError(f"Requires {'callers.view' if need is READ else min(need)}", 403)
        return business
    if needs == (READ,):
        if not reads_business(caller, business):
            raise PlatformError(CANNOT_READ, 403)
    elif caller.person_scopes is not None and not holds(caller.person_scopes, "Businesses.Write"):
        raise PlatformError("Requires Businesses.Write", 403)
    return business


async def branch_limit(businesses: BusinessService, business: Business, caller: Caller) -> list[str] | None:
    """The branches a business member is limited to, or None for every branch."""
    if not caller.business_id or not caller.uid:
        return None
    member = await businesses.business_db(business).get_row(BusinessDb.USERS, caller.uid) or {}
    limit = [b for b in member.get("branchIds") or [] if isinstance(b, str)]
    return limit or None


async def audit(businesses: BusinessService, business: Business, caller: Caller,
                action: str, data: dict) -> None:
    """One line in the business's own `audit/`, next to the console's."""
    await businesses.business_db(business).set_row(BusinessDb.AUDIT, uuid.uuid4().hex, {
        "action": action, "by": caller.uid or caller.app_id, "serviceperson": False, "data": data,
        "at": datetime.now(UTC),
    })


@router.get("")
async def list_callers(
    business_id: str, businesses: BusinessDep, callers: CallersDep,
    q: str = Query(default="", max_length=80), tag: str = Query(default="", max_length=80),
    blocked: bool = Query(default=False), limit: int = Query(default=0, ge=0),
    before: str = Query(default="", max_length=80),
    caller: Caller = requires_caller(Scope.HUB_BUSINESS_VIEW),
) -> dict:
    """By number or name prefix (`q`), by one `tag`, only `blocked` ones, or most recent call first."""
    business = await allowed(businesses, caller, business_id, READ)
    if sum(map(bool, (q.strip(), tag.strip(), blocked))) > 1:
        raise PlatformError("Search by one of q, tag or blocked", 422)
    size = min(limit or tunable("callers.pageSize"), tunable("callers.maxPageSize"))
    query = CallerQuery(q=q.strip(), tag=clean_tag(tag), blocked=blocked, limit=size, before=before)
    rows, cursor = await callers.search(business, query)
    return {"callers": rows, "next": cursor}


@router.post("")
async def block_number(
    business_id: str, body: CallerBlock, businesses: BusinessDep, callers: CallersDep,
    caller: Caller = requires_caller(Scope.HUB_BUSINESS_UPDATE),
) -> dict:
    """Blocks a number, creating its caller when it never called (only for people not limited to branches)."""
    business = await allowed(businesses, caller, business_id, BLOCK)
    limit = await branch_limit(businesses, business, caller)
    known = await callers.get(business, body.phone)
    if limit and (not known or known["branchId"] not in limit):
        raise PlatformError("Only callers of your branches", 403)
    by = caller.uid or caller.app_id
    saved = await callers.update(business, body.phone,
                                 block_fields(True, body.blockedReason, by, datetime.now(UTC)), create=True)
    data = {"phone": body.phone, "reason": body.blockedReason}
    await audit(businesses, business, caller, "caller.block", data)
    return saved or {}


@router.patch("/{phone}")
async def update_caller(
    business_id: str, body: CallerPatch, businesses: BusinessDep, callers: CallersDep,
    phone: str = Path(pattern=E164), caller: Caller = requires_caller(Scope.HUB_BUSINESS_UPDATE),
) -> dict:
    """Merges `name`, `tags` and/or `blocked`; the voice service never writes them."""
    needs = ([EDIT] if body.edits else []) + ([BLOCK] if body.blocked is not None else [])
    business = await allowed(businesses, caller, business_id, *needs)
    by, now = caller.uid or caller.app_id, datetime.now(UTC)
    fields = body.fields() | ({"updatedBy": by, "updatedAt": now} if body.edits else {})
    if body.blocked is not None:
        limit = await branch_limit(businesses, business, caller)
        known = await callers.get(business, phone)
        if known and limit and known["branchId"] not in limit:
            raise PlatformError("Only callers of your branches", 403)
        fields |= block_fields(body.blocked, body.blockedReason, by, now)
    saved = await callers.update(business, phone, fields)
    if saved is None:
        raise PlatformError("No such caller", 404)
    if body.blocked is not None:
        action = "caller.block" if body.blocked else "caller.unblock"
        await audit(businesses, business, caller, action, {"phone": phone, "reason": body.blockedReason})
    return saved
