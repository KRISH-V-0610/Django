"""
Small helpers that show F expressions (H) and database error handling (F/G).
No HTTP in here, so they are easy to test.
"""
from django.db import IntegrityError, transaction
from django.db.models import F

from .models import Product


# ------------------------------------------------ handling database rejections
def try_save(obj):
    """
    Save an object. Return None if it worked, or a short message if the
    DATABASE refused it (unique / check constraint broken).
    atomic() = savepoint, so a failed save does not break the outer transaction.
    """
    try:
        with transaction.atomic():
            obj.save()
    except IntegrityError as exc:
        return f"Rejected by the database: {exc.__cause__}".splitlines()[0]
    return None


# ----------------------------------------------------------- H. F expressions
def add_stock(product_id, amount):
    # UPDATE shop_product SET stock = stock + 5 WHERE id = ...   (done inside the database)
    Product.objects.filter(pk=product_id).update(stock=F("stock") + amount)


def remove_stock(product_id, amount):
    """
    Safe decrement. The filter `stock >= amount` and the subtraction happen in ONE
    SQL statement, so two buyers can never both take the last item.
    Returns True if it worked, False if there was not enough stock.
    """
    rows = Product.objects.filter(pk=product_id, stock__gte=amount).update(
        stock=F("stock") - amount
    )
    return rows == 1


def add_view(product_id):
    # counter: no read needed, no lost updates
    Product.objects.filter(pk=product_id).update(views=F("views") + 1)


def unsafe_remove_stock(product, amount):
    """
    The WRONG way (read -> change in Python -> write back).
    Kept only to demonstrate the lost-update race condition.
    `product` was read earlier, so its stock value may already be outdated.
    """
    product.stock = product.stock - amount
    product.save(update_fields=["stock"])
