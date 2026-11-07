import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from idempotency.models import IdempotencyRecord
from orders.models import Order
from transactions.models import Product

from .conftest import BASE, post


def place(client, auth, product, key, quantity=1, **extra):
    body = {"product_id": product.id, "quantity": quantity, **extra}
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return post(client, f"{BASE}/orders/place", body, **headers, **auth)


def test_missing_header_is_400(client, auth, product):
    assert place(client, auth, product, key=None).status_code == 400


def test_same_key_same_request_runs_once_and_replays(client, auth, product):
    first = place(client, auth, product, "key-1")
    second = place(client, auth, product, "key-1")

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == first.json()        # exact same saved answer

    assert Order.objects.count() == 1           # business operation ran ONCE
    product.refresh_from_db()
    assert product.stock == 5 - 1 - 1           # 1 bought + 1 sample, not doubled
    assert IdempotencyRecord.objects.get().status == "COMPLETED"


def test_same_key_different_payload_is_rejected(client, auth, product):
    place(client, auth, product, "key-1", quantity=1)
    response = place(client, auth, product, "key-1", quantity=2)

    assert response.status_code == 422
    assert Order.objects.count() == 1


def test_different_keys_make_different_orders(client, auth, product):
    place(client, auth, product, "key-1")
    place(client, auth, product, "key-2")
    assert Order.objects.count() == 2


def test_same_key_is_separate_per_user(client, auth, product, django_user_model):
    place(client, auth, product, "shared-key")

    from accounts.tokens import generate_token
    other = django_user_model.objects.create_user(username="other", password="Str0ng-pass-123")
    token, _, _ = generate_token(other.id, "access")
    place(client, {"HTTP_AUTHORIZATION": f"Bearer {token}"}, product, "shared-key")

    assert Order.objects.count() == 2


def test_key_still_processing_returns_409(client, auth, user, product):
    # pretend another request has claimed the key but not finished
    from idempotency.service import make_fingerprint
    from django.test import RequestFactory

    body = {"product_id": product.id, "quantity": 1, "fail_at": None}
    fake_request = RequestFactory().post(f"{BASE}/orders/place")
    IdempotencyRecord.objects.create(
        user=user, scope="place_order", key="busy",
        request_hash=make_fingerprint(fake_request, body),
        expires_at=timezone.now() + timedelta(hours=1),
    )

    response = place(client, auth, product, "busy")
    assert response.status_code == 409
    assert Order.objects.count() == 0


def test_failed_request_can_be_retried_with_same_key(client, auth, product):
    failed = place(client, auth, product, "key-1", fail_at="after_stock")
    assert failed.status_code == 500

    retry = place(client, auth, product, "key-1")
    assert retry.status_code == 201
    assert Order.objects.count() == 1


def test_expired_key_can_be_used_again(client, auth, product):
    place(client, auth, product, "key-1")
    IdempotencyRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    place(client, auth, product, "key-1")
    assert Order.objects.count() == 2           # old key had expired, so it ran again


def test_cleanup_command_removes_only_expired_keys(client, auth, product):
    from django.core.management import call_command

    place(client, auth, product, "old")
    place(client, auth, product, "fresh")
    IdempotencyRecord.objects.filter(key="old").update(expires_at=timezone.now() - timedelta(hours=1))

    call_command("clean_idempotency_keys")
    assert list(IdempotencyRecord.objects.values_list("key", flat=True)) == ["fresh"]


# ---- the real concurrency test: needs real commits, so transaction=True -----

@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_requests_run_the_operation_once(user, auth):
    product = Product.objects.create(name="Mouse", price="5.00", stock=10)
    barrier = threading.Barrier(5)              # make all threads fire together
    statuses = []

    def worker():
        try:
            barrier.wait()
            response = place(Client(), auth, product, "same-key")
            statuses.append(response.status_code)
        finally:
            connection.close()                  # each thread has its own DB connection

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(statuses) == 5
    assert statuses.count(201) >= 1
    assert set(statuses) <= {201, 409}          # winner/replays = 201, others "in progress" = 409
    assert Order.objects.count() == 1           # THE important check: one order only
    product.refresh_from_db()
    assert product.stock == 10 - 1 - 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_orders_never_oversell(user, auth):
    product = Product.objects.create(name="Last one", price="5.00", stock=3)
    barrier = threading.Barrier(6)
    statuses = []

    def worker(n):
        try:
            barrier.wait()
            # different keys -> six real, separate purchase attempts of 1 item each
            response = place(Client(), auth, product, f"key-{n}")
            statuses.append(response.status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    product.refresh_from_db()
    assert product.stock >= 0                   # never negative
    assert Order.objects.count() == statuses.count(201)
