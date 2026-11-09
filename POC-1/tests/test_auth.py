import time

import jwt
from django.conf import settings

from accounts.tokens import generate_token

from .conftest import BASE, post


def test_protected_endpoints_need_a_token(client):
    assert client.get(f"{BASE}/orders/").status_code == 401
    assert client.get(f"{BASE}/products/").status_code == 401
    assert client.get(f"{BASE}/auth/profile").status_code == 401


def test_valid_token_works(client, auth):
    assert client.get(f"{BASE}/orders/", **auth).status_code == 200


def test_expired_access_token_is_rejected(client, user):
    expired = jwt.encode(
        {"user_id": user.id, "token_type": "access", "exp": int(time.time()) - 10},
        settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )
    response = client.get(f"{BASE}/orders/", HTTP_AUTHORIZATION=f"Bearer {expired}")
    assert response.status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client, user):
    refresh, _, _ = generate_token(user.id, "refresh")
    response = client.get(f"{BASE}/orders/", HTTP_AUTHORIZATION=f"Bearer {refresh}")
    assert response.status_code == 401


def test_token_with_wrong_signature_is_rejected(client, user):
    fake = jwt.encode(
        {"user_id": user.id, "token_type": "access", "exp": int(time.time()) + 100},
        "some-other-secret-that-is-long-enough-1234",
        algorithm="HS256",
    )
    assert client.get(f"{BASE}/orders/", HTTP_AUTHORIZATION=f"Bearer {fake}").status_code == 401


def test_refresh_rotation_and_reuse(client, user):
    login = post(client, f"{BASE}/auth/login", {"username": "krish", "password": "Str0ng-pass-123"})
    assert login.status_code == 200
    old_refresh = login.json()["refresh"]

    first = post(client, f"{BASE}/auth/refresh", {"refresh": old_refresh})
    assert first.status_code == 200
    assert first.json()["refresh"] != old_refresh

    # the old refresh token was rotated out, so using it again must fail
    again = post(client, f"{BASE}/auth/refresh", {"refresh": old_refresh})
    assert again.status_code == 401


def test_logout_revokes_refresh_token(client, user):
    login = post(client, f"{BASE}/auth/login", {"username": "krish", "password": "Str0ng-pass-123"})
    refresh = login.json()["refresh"]

    assert post(client, f"{BASE}/auth/logout", {"refresh": refresh}).status_code == 200
    assert post(client, f"{BASE}/auth/refresh", {"refresh": refresh}).status_code == 401


def test_garbage_refresh_token_is_rejected(client, db):
    assert post(client, f"{BASE}/auth/refresh", {"refresh": "not-a-token"}).status_code == 401
