"""
The business operations in orders/services.py, tested against the real database.
The main point: every side effect is IDEMPOTENT, because Temporal retries activities.
"""
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F

from orders import services
from orders.models import NotificationLog, Order, Payment, Product, Reservation

pytestmark = pytest.mark.django_db


@pytest.fixture
def widget():
    return Product.objects.create(sku="WIDGET", name="Widget", price=Decimal("25.00"), stock=10)


@pytest.fixture
def order(widget):
    return services.create_order(sku="WIDGET", quantity=2, customer_email="a@example.com")


# ------------------------------------------------------------------ create / validate
def test_create_order_calculates_total(order):
    assert order.total_amount == Decimal("50.00")
    assert order.status == Order.Status.PENDING
    assert order.workflow_id == f"order-{order.id}"


def test_create_order_unknown_sku():
    with pytest.raises(Product.DoesNotExist):
        services.create_order(sku="NOPE", quantity=1, customer_email="a@example.com")


def test_validate_small_order_needs_no_approval(order):
    assert services.validate_order(order) is False


def test_validate_large_order_needs_approval(db):
    Product.objects.create(sku="PRICEY", name="Pricey", price=Decimal("1500.00"), stock=5)
    big = services.create_order(sku="PRICEY", quantity=1, customer_email="a@example.com")
    assert services.validate_order(big) is True


def test_validate_rejects_bad_email(widget):
    bad = services.create_order(sku="WIDGET", quantity=1, customer_email="not-an-email")
    with pytest.raises(services.InvalidOrder):
        services.validate_order(bad)


def test_check_availability(order):
    services.check_availability(order)  # 2 wanted, 10 in stock: fine
    Product.objects.filter(sku="WIDGET").update(stock=1)
    with pytest.raises(services.ProductUnavailable):
        services.check_availability(order)


# ------------------------------------------------------------------ reserve (idempotent)
def test_reserve_twice_gives_one_reservation_and_one_decrement(order, widget):
    assert services.reserve_inventory(order) is True
    assert services.reserve_inventory(order) is False  # the retry: already reserved

    widget.refresh_from_db()
    assert widget.stock == 8  # decremented once, not twice
    assert Reservation.objects.filter(order=order).count() == 1


def test_reserve_without_enough_stock_leaves_nothing_behind(order, widget):
    Product.objects.filter(pk=widget.pk).update(stock=1)
    with pytest.raises(services.ProductUnavailable):
        services.reserve_inventory(order)

    widget.refresh_from_db()
    assert widget.stock == 1
    assert Reservation.objects.filter(order=order).count() == 0  # rolled back with the failed update


def test_database_refuses_negative_stock(widget):
    """PositiveIntegerField adds a CHECK constraint: even a buggy update cannot go below zero."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=widget.pk).update(stock=F("stock") - 100)


# ------------------------------------------------------------------ payment (idempotent)
def test_charge_twice_gives_one_payment(order):
    first, created_first = services.charge_payment(order)
    second, created_second = services.charge_payment(order)

    assert created_first is True and created_second is False
    assert first.pk == second.pk
    assert Payment.objects.filter(order=order).count() == 1
    assert first.amount == Decimal("50.00")


# ------------------------------------------------------------------ compensation (idempotent)
def test_release_inventory_gives_stock_back_once(order, widget):
    services.reserve_inventory(order)
    assert services.release_inventory(order) is True
    assert services.release_inventory(order) is False  # second call: nothing left to undo

    widget.refresh_from_db()
    assert widget.stock == 10
    assert Reservation.objects.get(order=order).released is True


def test_release_without_reservation_is_harmless(order, widget):
    assert services.release_inventory(order) is False
    widget.refresh_from_db()
    assert widget.stock == 10


def test_refund_once(order):
    services.charge_payment(order)
    assert services.refund_payment(order) is True
    assert services.refund_payment(order) is False
    assert Payment.objects.get(order=order).status == Payment.Status.REFUNDED


def test_refund_without_payment_is_harmless(order):
    assert services.refund_payment(order) is False


# ------------------------------------------------------------------ status, notifications, report
def test_set_status_is_absolute(order):
    services.set_status(order.id, Order.Status.COMPLETED)
    services.set_status(order.id, Order.Status.COMPLETED)  # repeating changes nothing
    order.refresh_from_db()
    assert order.status == Order.Status.COMPLETED

    services.set_status(order.id, Order.Status.FAILED, "card declined")
    order.refresh_from_db()
    assert (order.status, order.failure_reason) == (Order.Status.FAILED, "card declined")


def test_record_notification_is_idempotent_by_workflow_id(db):
    services.record_notification("wf-1", "a@example.com", "Hello")
    services.record_notification("wf-1", "a@example.com", "Hello again")
    services.record_notification("wf-2", "b@example.com", "Other")

    assert NotificationLog.objects.count() == 2
    assert NotificationLog.objects.get(workflow_id="wf-1").subject == "Hello again"


def test_build_report_groups_by_status(widget):
    one = services.create_order(sku="WIDGET", quantity=1, customer_email="a@example.com")
    two = services.create_order(sku="WIDGET", quantity=2, customer_email="a@example.com")
    services.set_status(one.id, Order.Status.COMPLETED)
    services.set_status(two.id, Order.Status.COMPLETED)

    report = services.build_report()
    assert report["COMPLETED"] == {"orders": 2, "revenue": "75.00"}
