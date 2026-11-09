from decimal import Decimal

from django.core.management.base import BaseCommand

from orders.models import Product

# (sku, name, price, stock). Each one exists for a demo scenario.
PRODUCTS = [
    ("WIDGET", "Widget", "25.00", 10),  # normal order
    ("GADGET", "Gadget", "40.00", 2),  # only 2 in stock: ask for 5 -> "product unavailable"
    ("PRICEY", "Pricey thing", "1500.00", 5),  # total >= 1000 -> needs manual approval
    ("OUT-OF-STOCK", "Out of stock thing", "10.00", 0),
]


class Command(BaseCommand):
    help = "Create (or reset) the demo products used by the order demos."

    def handle(self, *args, **options):
        for sku, name, price, stock in PRODUCTS:
            product, created = Product.objects.update_or_create(
                sku=sku, defaults={"name": name, "price": Decimal(price), "stock": stock}
            )
            self.stdout.write(f"{'created' if created else 'reset  '} {product}")
