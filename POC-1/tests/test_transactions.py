import logging

import pytest
from django.db import transaction

from idempotency.models import IdempotencyRecord
from orders.models import Order
from transactions.models import Product, TransactionRecord
from transactions.services import SimulatedFailure, place_order

from .conftest import BASE, post


def place(client, auth, product, key, **extra):
    body = {"product_id": product.id, "quantity": 2, **extra}
    return post(client, f"{BASE}/orders/place", body, HTTP_IDEMPOTENCY_KEY=key, **auth)


# ---- success ----------------------------------------------------------------

def test_success_commits_everything(client, auth, product):
    response = place(client, auth, product, "k1")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "CONFIRMED"
    assert body["free_sample"] is True

    product.refresh_from_db()
    assert product.stock == 5 - 2 - 1          # 2 bought + 1 free sample
    assert Order.objects.get().status == "CONFIRMED"
    assert TransactionRecord.objects.count() == 1


# ---- rollback ---------------------------------------------------------------

@pytest.mark.parametrize("fail_at", ["after_stock", "after_record"])
def test_failure_rolls_back_everything(client, auth, product, fail_at):
    response = place(client, auth, product, "k1", fail_at=fail_at)

    assert response.status_code == 500
    product.refresh_from_db()
    assert product.stock == 5                   # stock restored
    assert Order.objects.count() == 0           # no half-made order
    assert TransactionRecord.objects.count() == 0


def test_not_enough_stock_changes_nothing(client, auth, product):
    response = place(client, auth, product, "k1", quantity=99)

    assert response.status_code == 400
    product.refresh_from_db()
    assert product.stock == 5
    assert Order.objects.count() == 0


def test_unknown_product_is_404(client, auth, db):
    response = post(client, f"{BASE}/orders/place", {"product_id": 999},
                    HTTP_IDEMPOTENCY_KEY="k1", **auth)
    assert response.status_code == 404


# ---- savepoint (nested atomic) ----------------------------------------------

def test_failed_nested_step_only_rolls_back_to_savepoint(client, auth, product):
    response = place(client, auth, product, "k1", fail_at="free_sample")

    assert response.status_code == 201          # main order still succeeded
    assert response.json()["free_sample"] is False
    product.refresh_from_db()
    assert product.stock == 5 - 2               # free sample was undone, purchase kept
    assert Order.objects.get().status == "CONFIRMED"


# ---- on_commit --------------------------------------------------------------

def test_on_commit_runs_after_commit(product, caplog, django_capture_on_commit_callbacks):
    with caplog.at_level(logging.WARNING):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            place_order(product.id, 1)

    assert len(callbacks) == 1
    assert "confirmation sent" in caplog.text


def test_on_commit_skipped_on_rollback(product, caplog, django_capture_on_commit_callbacks):
    with caplog.at_level(logging.WARNING):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(SimulatedFailure):
                place_order(product.id, 1, fail_at="after_record")

    assert callbacks == []
    assert "confirmation sent" not in caplog.text


# ---- manual exception handling trap -----------------------------------------

def test_swallowing_an_exception_inside_atomic_commits_the_half_work(product):
    """Shows WHY we never catch errors inside atomic(): the half-work is saved."""
    with transaction.atomic():
        Product.objects.filter(id=product.id).update(stock=0)
        try:
            raise SimulatedFailure()
        except SimulatedFailure:
            pass                                # swallowed -> no rollback!

    product.refresh_from_db()
    assert product.stock == 0


# ---- database constraint ----------------------------------------------------

def test_database_refuses_negative_stock(product):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Product.objects.filter(id=product.id).update(stock=-1)


def test_failed_request_leaves_no_idempotency_key(client, auth, product):
    place(client, auth, product, "k1", fail_at="after_stock")
    assert IdempotencyRecord.objects.count() == 0
