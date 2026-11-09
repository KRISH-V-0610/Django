import uuid

from django.db import models


class Product(models.Model):
    sku = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # PositiveIntegerField = the database itself refuses a negative stock (check constraint)
    stock = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.sku} (stock {self.stock})"


class Simulate(models.TextChoices):
    """
    Failure injection used by the demos and tests so every failure scenario of the
    assignment can be triggered on purpose. An empty value means "behave normally".
    """

    NONE = "", "no simulation"
    PAYMENT_DECLINED = "payment_declined", "card is declined (business error)"
    PAYMENT_API_DOWN = "payment_api_down", "payment API is down for the first attempts"
    PAYMENT_TIMEOUT = "payment_timeout", "charge succeeds but the response is lost (timeout)"
    EMAIL_DOWN = "email_down", "email service always fails"


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        AWAITING_APPROVAL = "AWAITING_APPROVAL"
        CONFIRMED = "CONFIRMED"  # inventory reserved and payment taken
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
        CANCELLED = "CANCELLED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="orders")
    quantity = models.PositiveIntegerField()
    customer_email = models.EmailField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    simulate = models.CharField(max_length=30, choices=Simulate.choices, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="order_quantity_gt_0"),
        ]

    @property
    def workflow_id(self) -> str:
        return f"order-{self.id}"

    def __str__(self):
        return f"{self.id} [{self.status}]"


class Reservation(models.Model):
    """One row per order. The unique constraint is what makes 'reserve' idempotent."""

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="reservation")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    released = models.BooleanField(default=False)  # True after compensation


class Payment(models.Model):
    """One row per order. The unique constraint is what makes 'charge' idempotent."""

    class Status(models.TextChoices):
        CHARGED = "CHARGED"
        REFUNDED = "REFUNDED"

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="payment")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CHARGED)
    created_at = models.DateTimeField(auto_now_add=True)


class NotificationLog(models.Model):
    """Written by the notification workflow. workflow_id is unique, so a retry never duplicates it."""

    workflow_id = models.CharField(max_length=200, unique=True)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
