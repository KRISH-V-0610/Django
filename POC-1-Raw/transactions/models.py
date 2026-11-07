from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField()

    class Meta:
        constraints = [
            # last line of defence: the database itself refuses negative stock
            models.CheckConstraint(
                condition=models.Q(stock__gte=0),
                name="product_stock_not_negative",
            )
        ]

    def __str__(self):
        return f"{self.name} (stock {self.stock})"


class TransactionRecord(models.Model):
    """One row per successful purchase - like a receipt."""

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
