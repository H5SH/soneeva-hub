"""/v1/advertisements: the master, its copies in each business, and who may see its images."""

from src.models.admin import Scope
from tests.test_media import PNG, biz, call, put  # noqa: F401

ADS = [Scope.HUB_ADVERTISEMENT_VIEW, Scope.HUB_ADVERTISEMENT_UPDATE]
URL = "/v1/advertisements"
AD = {"title": "Summer deal", "body": "Half off setup", "buttonLabel": "See", "buttonUrl": "https://soneeva.com/x"}


def _headers(bearer, uid: str = "admin", sub: str = "-"):
    return bearer("karbon-admin", ADS, sub=sub, uid=uid)


def test_publish_update_withdraw_and_delete_reach_every_copy(biz, client, bearer):  # noqa: F811
    made = client.post(URL, json=AD, headers=_headers(bearer))
    assert made.status_code == 201, made.text
    ad = made.json()
    assert ad["active"] and ad["businessIds"] == [] and ad["iconPath"] is None and ad["createdBy"] == "admin"
    url = f"{URL}/{ad['id']}"
    nowhere = client.post(f"{url}/publish", json={"businessIds": ["nope"]}, headers=_headers(bearer))
    assert nowhere.status_code == 404
    published = client.post(f"{url}/publish", json={"businessIds": ["biz1"]}, headers=_headers(bearer)).json()
    assert published["businessIds"] == ["biz1"]
    copies = biz.business_dbs["b-biz1"].rows
    copy = copies[("advertisements", ad["id"])]
    assert copy["title"] == "Summer deal" and copy["dismissed"] is False and copy["isDeleted"] is False
    assert copy["publishedAt"] and copy["dismissedAt"] is None and copy["backgroundPath"] is None
    copy["dismissed"] = True  # the business dismissed it
    patch = {"title": "Autumn deal", "active": False, "icon": True, "background": "image"}
    client.patch(url, json=patch, headers=_headers(bearer))
    copy = copies[("advertisements", ad["id"])]
    assert copy["title"] == "Autumn deal" and copy["active"] is False and copy["dismissed"] is True
    assert copy["iconPath"] == f"advertisements/{ad['id']}/icon"
    assert copy["backgroundPath"] == f"advertisements/{ad['id']}/background" and copy["background"] is None
    bad = client.patch(url, json={"background": "rainbow"}, headers=_headers(bearer))
    assert bad.status_code == 422
    withdrawn = client.delete(f"{url}/publish/biz1", headers=_headers(bearer)).json()
    assert withdrawn["businessIds"] == [] and copies[("advertisements", ad["id"])]["isDeleted"] is True
    client.post(f"{url}/publish", json={"businessIds": ["biz1"]}, headers=_headers(bearer))
    assert copies[("advertisements", ad["id"])]["dismissed"] is False  # published afresh
    assert client.delete(url, headers=_headers(bearer)).json() == {"deleted": True}
    assert copies[("advertisements", ad["id"])]["isDeleted"] is True
    assert biz.db.rows[("advertisements", ad["id"])]["isDeleted"] is True
    assert client.get(url, headers=_headers(bearer)).status_code == 404


def test_advertise_view_reads_and_write_changes(biz, client, bearer):  # noqa: F811
    biz.db.rows[("roles", "viewer")] = {"scopes": ["Advertise.View"]}
    biz.db.rows[("users", "u2")] = {"role": "viewer"}
    assert client.post(URL, json=AD, headers=_headers(bearer, "u2")).status_code == 403
    assert client.get(URL, headers=_headers(bearer, "u2")).json() == {"advertisements": []}
    assert client.get(URL, headers=_headers(bearer, "u1")).status_code == 403
    assert client.get(URL, headers=_headers(bearer, "boss", "biz1")).status_code == 403


def test_a_business_sees_an_ad_image_only_while_it_is_published_there(biz, client, bearer, call):  # noqa: F811
    ad = client.post(URL, json=AD | {"icon": True}, headers=_headers(bearer)).json()
    icon = f"advertisements/{ad['id']}/icon"
    assert put(call, icon, "u1").status_code == 403
    assert put(call, icon, "admin").status_code == 200
    assert call("GET", icon, "admin").status_code == 200
    assert call("GET", icon, "boss", "biz1").status_code == 403
    client.post(f"{URL}/{ad['id']}/publish", json={"businessIds": ["biz1"]}, headers=_headers(bearer))
    assert call("GET", icon, "boss", "biz1").status_code == 200
    assert put(call, icon, "boss", "biz1").status_code == 403


def test_the_contract_shapes(biz, client, bearer):  # noqa: F811
    from tests.test_contract import FIXTURE, same_keys

    made = client.post(URL, json=FIXTURE["advertisementCreate"], headers=_headers(bearer)).json()
    url = f"{URL}/{made['id']}"
    body = FIXTURE["advertisementPublish"] | {"businessIds": ["biz1"]}
    published = client.post(f"{url}/publish", json=body, headers=_headers(bearer)).json()
    same_keys(published, FIXTURE["advertisement"])
    same_keys(client.patch(url, json=FIXTURE["advertisementPatch"], headers=_headers(bearer)).json(),
              FIXTURE["advertisement"])
    same_keys(client.get(URL, headers=_headers(bearer)).json(), FIXTURE["advertisements"])
    copy = biz.business_dbs["b-biz1"].rows[("advertisements", made["id"])]
    assert set(copy) == set(FIXTURE["businessAdvertisement"])
