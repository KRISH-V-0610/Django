import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from accounts.tokens import generate_token
from transactions.models import Product

BASE = "/api/v1"


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    # real hashing is deliberately slow; tests don't need that
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="krish", password="Str0ng-pass-123")


@pytest.fixture
def auth(user):
    """Headers that a logged-in client would send."""
    token, _, _ = generate_token(user.id, "access")
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def product(db):
    return Product.objects.create(name="Keyboard", price="10.00", stock=5)


def post(client, url, body, **headers):
    """Small helper: POST a JSON body."""
    return client.post(url, data=json.dumps(body), content_type="application/json", **headers)
