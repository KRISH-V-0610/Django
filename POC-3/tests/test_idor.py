"""Topic D: IDOR. The same attack against the vulnerable and the secure endpoints."""
import logging

import pytest

from documents.models import Document

pytestmark = pytest.mark.django_db

JSON = "application/json"


@pytest.fixture
def alices_document(alice):
    return Document.objects.create(owner=alice, title="Alice salary", content="secret numbers")


# ------------------------------------------------------------------ the VULNERABLE version
def test_vulnerable_bob_can_read_alices_document(client, bob, auth, alices_document):
    response = client.get(f"/api/vulnerable/documents/{alices_document.id}", **auth(bob))
    assert response.status_code == 200
    assert response.json()["content"] == "secret numbers"  # the IDOR


def test_vulnerable_bob_can_change_alices_document(client, bob, auth, alices_document):
    response = client.put(
        f"/api/vulnerable/documents/{alices_document.id}",
        data={"title": "hacked", "content": "pwned"}, content_type=JSON, **auth(bob),
    )
    assert response.status_code == 200
    alices_document.refresh_from_db()
    assert alices_document.title == "hacked"


def test_vulnerable_bob_can_delete_alices_document(client, bob, auth, alices_document):
    response = client.delete(f"/api/vulnerable/documents/{alices_document.id}", **auth(bob))
    assert response.status_code == 204
    assert not Document.objects.filter(pk=alices_document.id).exists()


def test_vulnerable_endpoint_still_needs_a_login(client, alices_document):
    assert client.get(f"/api/vulnerable/documents/{alices_document.id}").status_code == 401


# ------------------------------------------------------------------ the SECURE version
def test_secure_owner_can_read_own_document(client, alice, auth, alices_document):
    response = client.get(f"/api/documents/{alices_document.id}", **auth(alice))
    assert response.status_code == 200
    assert response.json()["title"] == "Alice salary"


def test_secure_bob_cannot_read_alices_document(client, bob, auth, alices_document):
    response = client.get(f"/api/documents/{alices_document.id}", **auth(bob))
    assert response.status_code == 404
    assert "secret numbers" not in response.text


def test_secure_bob_cannot_change_alices_document(client, bob, auth, alices_document):
    response = client.put(
        f"/api/documents/{alices_document.id}",
        data={"title": "hacked", "content": "pwned"}, content_type=JSON, **auth(bob),
    )
    assert response.status_code == 404
    alices_document.refresh_from_db()
    assert alices_document.title == "Alice salary"


def test_secure_bob_cannot_delete_alices_document(client, bob, auth, alices_document):
    response = client.delete(f"/api/documents/{alices_document.id}", **auth(bob))
    assert response.status_code == 404
    assert Document.objects.filter(pk=alices_document.id).exists()


def test_secure_someone_elses_document_looks_like_a_missing_one(client, bob, auth, alices_document):
    """Same status and same body, so an attacker cannot tell which ids exist."""
    other = client.get(f"/api/documents/{alices_document.id}", **auth(bob))
    missing = client.get("/api/documents/999999", **auth(bob))
    assert (other.status_code, other.json()) == (missing.status_code, missing.json())


def test_secure_list_only_shows_own_documents(client, alice, bob, auth, alices_document):
    Document.objects.create(owner=bob, title="Bob note")

    titles = [d["title"] for d in client.get("/api/documents", **auth(bob)).json()]
    assert titles == ["Bob note"]


def test_secure_create_always_belongs_to_the_caller(client, alice, bob, auth):
    response = client.post(
        "/api/documents", data={"title": "mine", "owner_id": alice.id}, content_type=JSON, **auth(bob)
    )
    assert response.status_code == 201
    assert response.json()["owner_id"] == bob.id  # an owner_id sent by the client is ignored


def test_secure_endpoint_needs_a_login(client, alices_document):
    assert client.get(f"/api/documents/{alices_document.id}").status_code == 401


def test_blocked_idor_attempt_is_logged(client, bob, auth, alices_document, caplog):
    with caplog.at_level(logging.WARNING, logger="security"):
        client.get(f"/api/documents/{alices_document.id}", **auth(bob))

    line = caplog.text
    assert "event='idor_blocked'" in line
    assert f"document_id={alices_document.id}" in line
    assert "user='bob'" in line
