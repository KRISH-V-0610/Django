"""Topic I (Q objects) and the HTTP API that uses everything."""
import json
from decimal import Decimal

import pytest
from django.db.models import Q
from django.test import Client

from shop.filters import build_product_filter, search_products
from shop.models import Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def products():
    def make(sku, name, price, stock=1, active=True):
        return Product.objects.create(
            sku=sku, name=name, price=Decimal(price), stock=stock, is_active=active
        )
    make("PEN-1", "Blue Pen", "10")
    make("PEN-2", "Red Pen", "20", stock=0)
    make("BOOK-1", "Python Book", "500")
    make("BOOK-2", "Django Book", "700", stock=0)
    make("OLD-1", "Old Pen", "5", active=False)


def names(qs):
    return sorted(p.name for p in qs)


def test_or_across_fields(products):
    assert names(search_products(search="book")) == ["Django Book", "Python Book"]
    assert names(search_products(search="pen-1")) == ["Blue Pen"]  # matched by SKU, not name


def test_and_conditions(products):
    assert names(search_products(search="pen", min_price=15)) == ["Red Pen"]
    assert names(search_products(search="book", in_stock=True)) == ["Python Book"]


def test_not_condition(products):
    assert names(search_products(search="pen", exclude="red")) == ["Blue Pen"]


def test_dynamic_or_from_a_list(products):
    assert names(search_products(names=["blue", "django"])) == ["Blue Pen", "Django Book"]


def test_only_active_switch(products):
    assert "Old Pen" not in names(search_products(search="pen"))
    assert "Old Pen" in names(search_products(search="pen", only_active=False))


def test_no_filters_returns_all_active(products):
    assert len(search_products()) == 4


def test_nested_condition_written_by_hand(products):
    # (pen AND price<15) OR (book AND NOT django)
    cond = (Q(name__icontains="pen") & Q(price__lt=15)) | (
        Q(name__icontains="book") & ~Q(name__icontains="django")
    )
    assert names(Product.objects.filter(cond, is_active=True)) == ["Blue Pen", "Python Book"]


def test_empty_q_means_no_condition():
    assert str(build_product_filter(only_active=False)) == str(Q())


# ------------------------------------------------------------------- API
def post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


def test_api_customer_duplicate_email_gives_friendly_409():
    client = Client()
    ok = post(client, "/api/customers", {"name": "A", "email": "a@x.com"})
    dup = post(client, "/api/customers", {"name": "B", "email": "a@x.com"})
    assert ok.status_code == 201
    assert dup.status_code == 409
    assert "already exists" in dup.json()["detail"]


def test_api_product_uses_uuid7_and_database_rules():
    client = Client()
    ok = post(client, "/api/products", {"sku": "A", "name": "Pen", "price": "9.5", "stock": 3})
    assert ok.status_code == 201
    assert ok.json()["id"][14] == "7"  # 15th char of a UUID string is its version

    free = post(client, "/api/products", {"sku": "B", "name": "Free", "price": "0"})
    assert free.status_code == 400  # active product with price 0: check constraint
    assert "active_product_needs_price" in free.json()["detail"]


def test_api_stock_endpoints_and_view_counter():
    client = Client()
    pid = post(client, "/api/products", {"sku": "A", "name": "Pen", "price": "9", "stock": 2}).json()["id"]

    assert post(client, f"/api/products/{pid}/add-stock", {"amount": 3}).json()["stock"] == 5
    assert post(client, f"/api/products/{pid}/remove-stock", {"amount": 5}).json()["stock"] == 0
    assert post(client, f"/api/products/{pid}/remove-stock", {"amount": 1}).status_code == 400

    client.get(f"/api/products/{pid}")
    assert client.get(f"/api/products/{pid}").json()["views"] == 2


def test_api_search_with_q_filters(products):
    client = Client()
    r = client.get("/api/products/search", {"search": "pen", "exclude": "red", "in_stock": "true"})
    assert [p["name"] for p in r.json()] == ["Blue Pen"]

    r = client.get("/api/products/search", {"names": ["blue", "django"]})
    assert sorted(p["name"] for p in r.json()) == ["Blue Pen", "Django Book"]
