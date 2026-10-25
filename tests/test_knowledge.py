"""/v1/knowledge/read: a photo or PDF read into knowledge text or menu rows, by those who edit them."""

import pytest

from src.models.admin import Scope
from tests.test_demo_settings import staff
from tests.test_media import PNG, biz  # noqa: F401  (fixture)

URL = "/v1/knowledge/read"
PDF = b"%PDF-1.7\n" + b"0" * 32


@pytest.fixture
def send(client, bearer):
    def run(uid, sub="-", body=PNG, kind="image/png", form="text", app="karbon-admin"):
        headers = bearer(app, [Scope.HUB_KNOWLEDGE_READ], sub=sub, uid=uid) | {"Content-Type": kind}
        return client.post(f"{URL}?form={form}", content=body, headers=headers)

    return run


def test_a_demo_line_editor_reads_a_document_into_text(send, services, bearer):
    staff(services, bearer, "sales2", ["Demo.Number.Write"])
    staff(services, bearer, "page", ["Demo.Page.Write"])
    assert send("sales2").json() == {"text": "Margherita: 9.50"}
    assert send("sales2", body=PDF, kind="application/pdf").status_code == 200
    assert services.reader.read == [("image/png", len(PNG)), ("application/pdf", len(PDF))]
    assert send("page").status_code == 403  # edits no line


def test_a_business_menu_editor_reads_a_menu_into_rows(biz, send):  # noqa: F811
    biz.business_dbs["b-biz1"].rows[("roles", "owner")]["scopes"].append("menu.edit")
    rows = send("boss", "biz1", form="menu", app="console").json()["items"]
    assert rows == [
        {"name": "Margherita", "category": "Pizza", "price": 9.5, "description": "", "available": True}]
    assert send("clerk", "biz1", form="menu", app="console").status_code == 403


def test_only_images_and_pdfs_within_the_limit(send, services, bearer):
    staff(services, bearer, "sales2", ["Demo.Number.Write"])
    assert send("sales2", body=b"hello", kind="text/plain").status_code == 415
    assert send("sales2", body=b"not a pdf", kind="application/pdf").status_code == 415
    assert send("sales2", body=PDF + b"0" * 10_485_760, kind="application/pdf").status_code == 413
    assert services.reader.read == []


def test_the_app_needs_the_scope(client, bearer, services):
    staff(services, bearer, "sales2", ["Demo.Number.Write"])
    headers = bearer("other-app", [Scope.HUB_DEMO_UPDATE], uid="sales2") | {"Content-Type": "image/png"}
    assert client.post(URL, content=PNG, headers=headers).status_code == 403
