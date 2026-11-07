"""
I. Q objects: build one query condition from optional pieces.
"""
from functools import reduce
from operator import or_

from django.db.models import Q

from .models import Product


def build_product_filter(
    search=None,        # text matched in name OR sku
    names=None,         # list of names, matches ANY of them (dynamic OR)
    exclude=None,       # text that must NOT be in the name (NOT)
    min_price=None,
    max_price=None,
    in_stock=False,
    only_active=True,
) -> Q:
    condition = Q()  # empty Q = "no condition"

    if search:
        # OR across two fields
        condition &= Q(name__icontains=search) | Q(sku__icontains=search)

    if names:
        # OR built dynamically from a list: name1 OR name2 OR name3 ...
        condition &= reduce(or_, [Q(name__icontains=n) for n in names])

    if exclude:
        # NOT
        condition &= ~Q(name__icontains=exclude)

    # AND conditions
    if min_price is not None:
        condition &= Q(price__gte=min_price)
    if max_price is not None:
        condition &= Q(price__lte=max_price)
    if in_stock:
        condition &= Q(stock__gt=0)
    if only_active:
        condition &= Q(is_active=True)

    return condition


def search_products(**filters):
    return Product.objects.filter(build_product_filter(**filters)).order_by("name")
