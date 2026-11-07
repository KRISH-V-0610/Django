import uuid

from django.db import models
from django.db.models import F, Q


# ---------------------------------------------------------------- A. UUIDv7
# Two tiny models that only differ in the default of the primary key.
# v4 = fully random, v7 = starts with a timestamp (so new rows sort after old rows).

class DemoV4(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(max_length=50)


class DemoV7(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    label = models.CharField(max_length=50)


# ------------------------------------------------- F. Unique constraints
class Customer(models.Model):
    name = models.CharField(max_length=100)
    # single column unique: simplest form
    email = models.EmailField(unique=True)

    def __str__(self):
        return self.email


# ---------------------------------------------------- G. Check constraints
class Account(models.Model):
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(balance__gte=0),
                name="account_balance_not_negative",
            )
        ]


class Product(models.Model):
    # UUIDv7 as the primary key (topic A used in a real model)
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    sku = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # IntegerField (not Positive...) on purpose, so the CHECK constraint below is the one that protects it
    stock = models.IntegerField(default=0)
    views = models.IntegerField(default=0)  # a counter, updated with F()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            # conditional unique: an SKU must be unique only among ACTIVE products
            models.UniqueConstraint(
                fields=["sku"],
                condition=Q(is_active=True),
                name="unique_active_sku",
            ),
            # stock can never be negative
            models.CheckConstraint(
                condition=Q(stock__gte=0),
                name="product_stock_not_negative",
            ),
            # business rule: an active product must have a price above zero
            # ("inactive OR price > 0")
            models.CheckConstraint(
                condition=Q(is_active=False) | Q(price__gt=0),
                name="active_product_needs_price",
            ),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"


class OrderItem(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name="order_item_quantity_positive",
            )
        ]


class PriceRange(models.Model):
    name = models.CharField(max_length=50)
    min_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_value = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            # compares two columns of the same row, so it uses F()
            models.CheckConstraint(
                condition=Q(min_value__lte=F("max_value")),
                name="price_range_min_not_above_max",
            )
        ]


class Review(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    rating = models.IntegerField()

    class Meta:
        constraints = [
            # multi column unique: one review per customer per product
            models.UniqueConstraint(
                fields=["customer", "product"],
                name="one_review_per_customer_per_product",
            ),
            models.CheckConstraint(
                condition=Q(rating__gte=1) & Q(rating__lte=5),
                name="review_rating_1_to_5",
            ),
        ]
