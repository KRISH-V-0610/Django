from django.db import models


class Order(models.Model):

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"

    product_name = models.CharField(max_length=100)

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    # used by the "place order" workflow. null/default so plain CRUD orders still work.
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    product = models.ForeignKey("transactions.Product", null=True, blank=True, on_delete=models.SET_NULL)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"Order {self.id}: {self.product_name} - ${self.amount}"
