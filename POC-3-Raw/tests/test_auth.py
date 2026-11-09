"""Registration, login, JWT checks and brute-force protection (OWASP: authentication)."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from django.conf import settings

from accounts.services import MAX_FAILED

pytestmark = pytest.mark.django_db

JSON = "application/json"


def post(client, url, **body):
    return client.post(url, data=body, content_type=JSON)


def test_register_creates_a_user_with_a_hashed_password(client):
    response = post(client, "/api/auth/register", username="carol", password="a-long-test-password")
    assert response.status_code == 201
    assert response.json()["username"] == "carol"

    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(username="carol")
    assert user.password != "a-long-test-password"  # never stored as plain text
    assert user.check_password("a-long-test-password")


def test_register_rejects_a_weak_password(client):
    response = post(client, "/api/auth/register", username="carol", password="12345678")
    assert response.status_code == 400


def test_register_rejects_a_duplicate_username(client, alice):
    response = post(client, "/api/auth/register", username="alice", password="a-long-test-password")
    assert response.status_code == 409


def test_login_returns_a_token_that_works(client, alice):
    response = post(client, "/api/auth/login", username="alice", password="a-long-test-password")
    assert response.status_code == 200
    token = response.json()["access"]

    documents = client.get("/api/documents", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert documents.status_code == 200


def test_wrong_password_and_unknown_user_look_identical(client, alice):
    wrong_password = post(client, "/api/auth/login", username="alice", password="nope-nope-nope")
    unknown_user = post(client, "/api/auth/login", username="nobody", password="nope-nope-nope")

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()  # no user enumeration


def test_account_is_locked_after_too_many_failures(client, alice):
    for _ in range(MAX_FAILED):
        assert post(client, "/api/auth/login", username="alice", password="wrong-wrong").status_code == 401

    # even the CORRECT password is refused now
    locked = post(client, "/api/auth/login", username="alice", password="a-long-test-password")
    assert locked.status_code == 429


def test_lock_is_per_username(client, alice, bob):
    for _ in range(MAX_FAILED):
        post(client, "/api/auth/login", username="alice", password="wrong-wrong")

    response = post(client, "/api/auth/login", username="bob", password="a-long-test-password")
    assert response.status_code == 200


def test_successful_login_resets_the_failure_counter(client, alice):
    for _ in range(MAX_FAILED - 1):
        post(client, "/api/auth/login", username="alice", password="wrong-wrong")
    assert post(client, "/api/auth/login", username="alice", password="a-long-test-password").status_code == 200

    for _ in range(MAX_FAILED - 1):  # counting starts again from zero
        assert post(client, "/api/auth/login", username="alice", password="wrong-wrong").status_code == 401


def test_missing_token_is_401(client):
    assert client.get("/api/documents").status_code == 401


def test_garbage_token_is_401(client):
    assert client.get("/api/documents", HTTP_AUTHORIZATION="Bearer not.a.token").status_code == 401


def make_token(user_id, secret=None, expires_in=300):
    payload = {"user_id": user_id, "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in)}
    return jwt.encode(payload, secret or settings.JWT_SECRET_KEY, algorithm="HS256")


def test_expired_token_is_401(client, alice):
    token = make_token(alice.id, expires_in=-10)
    assert client.get("/api/documents", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401


def test_token_signed_with_another_secret_is_401(client, alice):
    token = make_token(alice.id, secret="x" * 40)
    assert client.get("/api/documents", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401


def test_token_without_signature_is_401(client, alice):
    """The classic 'alg: none' attack: a token that claims it needs no signature."""
    token = jwt.encode({"user_id": alice.id}, key=None, algorithm="none")
    assert client.get("/api/documents", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401


def test_token_of_a_deactivated_user_is_401(client, alice):
    token = make_token(alice.id)
    alice.is_active = False
    alice.save()
    assert client.get("/api/documents", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401
