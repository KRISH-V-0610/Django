"""Topic H: F expressions."""
import threading
from decimal import Decimal

import pytest
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from shop import services
from shop.models import Product


def make_product(stock=10):
    return Product.objects.create(sku="S", name="Pen", price=Decimal("10"), stock=stock)


@pytest.mark.django_db
def test_add_and_remove_stock():
    p = make_product(stock=10)
    services.add_stock(p.pk, 5)
    assert services.remove_stock(p.pk, 3) is True
    p.refresh_from_db()
    assert p.stock == 12


@pytest.mark.django_db
def test_remove_stock_refuses_to_go_below_zero():
    p = make_product(stock=2)
    assert services.remove_stock(p.pk, 3) is False
    p.refresh_from_db()
    assert p.stock == 2


@pytest.mark.django_db
def test_python_side_update_loses_an_update_database_side_does_not():
    """Deterministic version of the race: two 'requests' read the same stock first."""
    p = make_product(stock=10)

    # WRONG: both read stock=10, both write 10-3=7. One removal is lost.
    first, second = Product.objects.get(pk=p.pk), Product.objects.get(pk=p.pk)
    services.unsafe_remove_stock(first, 3)
    services.unsafe_remove_stock(second, 3)
    p.refresh_from_db()
    assert p.stock == 7  # should be 4

    # RIGHT: each UPDATE uses the current value inside the database.
    p.stock = 10
    p.save()
    services.remove_stock(p.pk, 3)
    services.remove_stock(p.pk, 3)
    p.refresh_from_db()
    assert p.stock == 4


@pytest.mark.django_db
def test_f_expression_generates_one_sql_update():
    p = make_product()
    # the arithmetic is part of the UPDATE statement itself, not Python
    with CaptureQueriesContext(connection) as ctx:
        Product.objects.filter(pk=p.pk).update(views=F("views") + 1)
    assert len(ctx.captured_queries) == 1
    assert '"views" + 1' in ctx.captured_queries[0]["sql"]


def run_in_threads(count, target):
    errors = []
    barrier = threading.Barrier(count)

    def worker():
        try:
            barrier.wait()  # start all threads at the same moment
            target()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()  # each thread has its own DB connection

    threads = [threading.Thread(target=worker) for _ in range(count)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors


@pytest.mark.django_db(transaction=True)
def test_concurrent_counter_increments_are_not_lost():
    p = make_product()

    def work():
        for _ in range(10):
            services.add_view(p.pk)

    run_in_threads(10, work)  # 10 threads x 10 increments
    p.refresh_from_db()
    assert p.views == 100


@pytest.mark.django_db(transaction=True)
def test_concurrent_buyers_never_oversell():
    p = make_product(stock=5)
    results = []

    def work():
        results.append(services.remove_stock(p.pk, 1))

    run_in_threads(10, work)  # 10 buyers, only 5 items
    p.refresh_from_db()
    assert results.count(True) == 5 and results.count(False) == 5
    assert p.stock == 0
