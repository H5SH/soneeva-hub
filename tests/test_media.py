"""/v1/media: fixed paths, each checked against the acting person."""

import pytest

from src.models.admin import Scope
from src.models.db import Default
from tests.setup.people import people

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
APP = [Scope.HUB_MEDIA_VIEW, Scope.HUB_MEDIA_UPDATE]


@pytest.fixture
def biz(services):
    """biz1: boss (owner), clerk (staff, branches.view), serviceperson sp; platform people from people()."""
    people(services)
    services.db.rows[(Default.BUSINESSES.value, "biz1")] = {
        "name": "B", "type": "restaurant", "appId": "soneeva", "dbId": "b-biz1", "ownerUid": "boss",
        "servicepersonUid": "sp",
    }
    bdb = services.business_dbs.setdefault("b-biz1", type(services.db)())
    bdb.rows[("roles", "owner")] = {"scopes": ["business.edit", "branches.edit", "team.edit", "profile.edit"]}
    bdb.rows[("roles", "staff")] = {"scopes": ["overview.view", "branches.view"]}
    for uid, role in (("boss", "owner"), ("clerk", "staff")):
        services.accounts.add(uid, businessId="biz1")
        bdb.rows[("users", uid)] = {"role": role}
    services.db.rows[("users", "admin")] = {"role": "superadmin"}
    return services


@pytest.fixture
def call(client, bearer):
    def run(method: str, path: str, uid: str, sub: str = "-", **kwargs):
        headers = bearer("console", APP, sub=sub, uid=uid) | kwargs.pop("headers", {})
        return client.request(method, f"/v1/media/{path}", headers=headers, **kwargs)

    return run


def put(call, path: str, uid: str, sub: str = "-", body: bytes = PNG, kind: str = "image/png"):
    return call("PUT", path, uid, sub, content=body, headers={"Content-Type": kind})


def test_a_business_logo_is_changed_by_business_edit_and_seen_by_members(biz, call):
    logo = "businesses/biz1/avatar"
    assert call("GET", logo, "clerk", "biz1").status_code == 404
    assert put(call, logo, "clerk", "biz1").status_code == 403
    saved = put(call, logo, "boss", "biz1")
    assert saved.status_code == 200 and saved.json()["path"] == logo and saved.json()["updatedAt"]
    got = call("GET", logo, "clerk", "biz1")
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    etag = got.headers["etag"]
    assert call("GET", logo, "clerk", "biz1", headers={"If-None-Match": etag}).status_code == 304
    assert call("GET", logo, "u1").status_code == 403
    assert call("GET", logo, "admin").status_code == 200
    assert call("DELETE", logo, "boss", "biz1").json() == {"deleted": True}
    assert call("GET", logo, "boss", "biz1").status_code == 404


def test_branch_logos_follow_the_branch_scopes(biz, call):
    logo = "businesses/biz1/branches/main/avatar"
    assert put(call, logo, "clerk", "biz1").status_code == 403
    assert put(call, logo, "boss", "biz1").status_code == 200
    assert call("GET", logo, "clerk", "biz1").status_code == 200
    assert call("GET", logo, "u1").status_code == 403


def test_businesses_access_sees_the_logos_of_businesses_assigned_to_the_person(biz, call):
    biz.db.rows[("roles", "access")] = {"scopes": ["Businesses.Access"]}
    for uid in ("sp", "u2"):  # biz1 is assigned to sp only
        biz.db.rows[("users", uid)]["role"] = "access"
    for logo in ("businesses/biz1/avatar", "businesses/biz1/branches/main/avatar"):
        assert put(call, logo, "boss", "biz1").status_code == 200
        assert call("GET", logo, "sp").status_code == 200
        assert call("GET", logo, "u2").status_code == 403


def test_avatars_for_yourself_your_business_and_whom_you_look_after(biz, call):
    assert put(call, "users/clerk/avatar", "clerk", "biz1").status_code == 403
    assert put(call, "users/boss/avatar", "boss", "biz1").status_code == 200
    assert put(call, "users/clerk/avatar", "boss", "biz1").status_code == 200
    assert call("GET", "users/boss/avatar", "clerk", "biz1").status_code == 200
    assert call("GET", "users/sp/avatar", "clerk", "biz1").status_code == 404
    assert call("GET", "users/u1/avatar", "clerk", "biz1").status_code == 403
    assert put(call, "users/u1/avatar", "u1").status_code == 403
    biz.db.rows[("roles", "user")] = {"scopes": ["Profile.Write"]}
    assert put(call, "users/u1/avatar", "u1").status_code == 200
    assert call("GET", "users/u1/avatar", "sp").status_code == 200
    assert call("GET", "users/u2/avatar", "sp").status_code == 403
    assert call("GET", "users/u1/avatar", "admin").status_code == 200


def test_app_icons_need_the_payment_app_scopes(biz, call):
    assert put(call, "apps/soneeva/avatar", "admin").status_code == 200
    assert call("GET", "apps/soneeva/avatar", "admin").status_code == 200
    assert call("GET", "apps/soneeva/avatar", "sp").status_code == 403
    assert put(call, "apps/soneeva/avatar", "boss", "biz1").status_code == 403


def test_only_small_images_at_known_paths(biz, call, monkeypatch):
    assert put(call, "users/boss/avatar.png", "boss", "biz1").status_code == 404
    assert put(call, "businesses/biz1/menu", "boss", "biz1").status_code == 404
    assert put(call, "users/boss/avatar", "boss", "biz1", kind="image/gif").status_code == 415
    assert put(call, "users/boss/avatar", "boss", "biz1", kind="image/jpeg").status_code == 415
    assert put(call, "users/boss/avatar", "boss", "biz1", body=PNG + b"0" * 2_000_000).status_code == 413
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    assert put(call, "users/boss/avatar", "boss", "biz1", body=webp, kind="image/webp").status_code == 200


def test_the_app_scopes_are_required(biz, client, bearer):
    headers = lambda: bearer("bare", [Scope.HUB_MEDIA_VIEW], sub="biz1", uid="boss")  # noqa: E731
    assert client.get("/v1/media/businesses/biz1/avatar", headers=headers()).status_code == 404
    assert client.put("/v1/media/businesses/biz1/avatar", headers=headers(), content=PNG).status_code == 403


def test_servicepersons_are_seen_by_their_viewers(biz, call):
    biz.db.rows[("roles", "seller")] = {"scopes": ["Servicepersons.View"]}
    biz.db.rows[("users", "seller")] = {"role": "seller"}
    assert call("GET", "users/u2/avatar", "seller").status_code == 403
    assert call("GET", "users/sp/avatar", "seller").status_code == 404  # sp's role is serviceperson
    biz.db.rows[("users", "u2")]["role"] = "serviceperson"
    assert call("GET", "users/u2/avatar", "seller").status_code == 404
