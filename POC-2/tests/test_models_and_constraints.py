"""Topics A (uuid in models), F (unique) and G (check). Every failure here comes from POSTGRES."""
import uuid
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from shop.models import (
    Account, Customer, DemoV4, DemoV7, OrderItem, PriceRange, Product, Review,
)
from shop.services import try_save

pytestmark = pytest.mark.django_db


def make_product(**kw):
    data = {"sku": "SKU-1", "name": "Pen", "price": Decimal("10.00"), "stock": 5}
    data.update(kw)
    return Product.objects.create(**data)


def rejected(constraint_name, fn):
    """Run fn and check Postgres refused it because of THIS constraint."""
    with pytest.raises(IntegrityError) as exc:
        with transaction.atomic():  # savepoint, so the test can continue afterwards
            fn()
    assert constraint_name in str(exc.value)


# ------------------------------------------------------------- A. UUID in models
def test_uuid_primary_keys_are_generated_and_stored():
    v4, v7 = DemoV4.objects.create(label="a"), DemoV7.objects.create(label="a")
    assert v4.id.version == 4 and v7.id.version == 7

    assert DemoV7.objects.get(pk=v7.id).label == "a"  # retrieve by uuid
    assert isinstance(DemoV7.objects.get(pk=v7.id).id, uuid.UUID)


def test_v7_rows_come_back_in_insert_order_when_sorted_by_pk():
    ids = [DemoV7.objects.create(label=str(i)).id for i in range(30)]
    assert list(DemoV7.objects.order_by("pk").values_list("id", flat=True)) == ids


def test_postgres_has_a_unique_index_on_the_uuid_primary_key():
    with connection.cursor() as cur:
        cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'shop_demov7'")
        definitions = " ".join(row[0] for row in cur.fetchall())
    assert "UNIQUE INDEX" in definitions and "(id)" in definitions


# --------------------------------------------------------------- F. unique
def test_unique_email():
    Customer.objects.create(name="A", email="a@x.com")
    rejected("email", lambda: Customer.objects.create(name="B", email="a@x.com"))


def test_multi_field_unique_one_review_per_customer_per_product():
    c, p = Customer.objects.create(name="A", email="a@x.com"), make_product()
    Review.objects.create(customer=c, product=p, rating=5)
    rejected("one_review_per_customer_per_product",
             lambda: Review.objects.create(customer=c, product=p, rating=4))

    other = Customer.objects.create(name="B", email="b@x.com")
    Review.objects.create(customer=other, product=p, rating=4)  # different customer is fine


def test_conditional_unique_only_active_products_must_have_unique_sku():
    make_product(sku="DUP")
    rejected("unique_active_sku", lambda: make_product(sku="DUP"))  # active + active: refused

    make_product(sku="DUP", is_active=False)  # active + inactive: allowed
    make_product(sku="DUP", is_active=False)  # inactive + inactive: allowed
    assert Product.objects.filter(sku="DUP").count() == 3


def test_duplicate_insert_error_handling_helper():
    assert try_save(Customer(name="A", email="a@x.com")) is None
    message = try_save(Customer(name="B", email="a@x.com"))
    assert message and "Rejected by the database" in message
    assert Customer.objects.count() == 1  # the failed insert left nothing behind


# --------------------------------------------------------------- G. check
def test_balance_cannot_be_negative():
    c = Customer.objects.create(name="A", email="a@x.com")
    rejected("account_balance_not_negative",
             lambda: Account.objects.create(customer=c, balance=Decimal("-0.01")))
    Account.objects.create(customer=c, balance=Decimal("0"))  # zero is allowed


def test_quantity_must_be_positive():
    p = make_product()
    rejected("order_item_quantity_positive", lambda: OrderItem.objects.create(product=p, quantity=0))
    rejected("order_item_quantity_positive", lambda: OrderItem.objects.create(product=p, quantity=-3))
    OrderItem.objects.create(product=p, quantity=1)


def test_min_cannot_be_above_max():
    rejected("price_range_min_not_above_max",
             lambda: PriceRange.objects.create(name="bad", min_value=10, max_value=5))
    PriceRange.objects.create(name="ok", min_value=5, max_value=5)  # equal is allowed


def test_business_rule_active_product_needs_a_price():
    rejected("active_product_needs_price", lambda: make_product(price=Decimal("0")))
    make_product(sku="FREE", price=Decimal("0"), is_active=False)  # inactive may have price 0


def test_stock_and_rating_checks():
    rejected("product_stock_not_negative", lambda: make_product(stock=-1))
    c, p = Customer.objects.create(name="A", email="a@x.com"), make_product()
    rejected("review_rating_1_to_5", lambda: Review.objects.create(customer=c, product=p, rating=6))


def test_database_rejects_even_when_python_validation_is_skipped():
    """bulk_create and update() skip model.full_clean(); only the database still guards the rule."""
    p = make_product(stock=5)
    rejected("product_stock_not_negative", lambda: Product.objects.filter(pk=p.pk).update(stock=-5))
