"""
Business operations for orders. No Temporal and no HTTP in here.

Every function that has a side effect is IDEMPOTENT: calling it twice for the same
order gives the same end result as calling it once. Temporal retries activities, and a
retry can happen even after the first attempt already did its work (e.g. the response was
lost), so the database must be the one that refuses duplicates.
"""
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from .models import NotificationLog, Order, Payment, Product, Reservation


class InvalidOrder(Exception):
    """The order data itself is wrong. Retrying will never fix it."""


class ProductUnavailable(Exception):
    """Not enough stock. Retrying will not create stock."""


# ------------------------------------------------------------------ create
def create_order(*, sku, quantity, customer_email, simulate=""):
    """Create the order row (status PENDING). Raises Product.DoesNotExist for an unknown SKU."""
    product = Product.objects.get(sku=sku)
    return Order.objects.create(
        product=product,
        quantity=quantity,
        customer_email=customer_email,
        total_amount=product.price * quantity,
        simulate=simulate,
    )


# ---------------------------------------------------------------- workflow steps
def validate_order(order):
    """Step 3. Returns True when the order needs a manual approval (large total)."""
    if order.quantity < 1:
        raise InvalidOrder("quantity must be at least 1")
    if "@" not in order.customer_email:
        raise InvalidOrder("customer email is not valid")
    return order.total_amount >= Decimal(settings.ORDER_APPROVAL_THRESHOLD)


def check_availability(order):
    """Step 4. Read-only check. Raises ProductUnavailable if stock is too low."""
    product = Product.objects.get(pk=order.product_id)
    if product.stock < order.quantity:
        raise ProductUnavailable(
            f"{product.sku}: wanted {order.quantity}, only {product.stock} in stock"
        )


def reserve_inventory(order):
    """
    Step 5. Take the stock for this order. Returns True if reserved now, False if it was
    already reserved (a retry). The unique Reservation row is the guard against double
    reservation; stock is decremented with F + stock__gte in ONE statement (see POC 2).
    """
    try:
        with transaction.atomic():
            Reservation.objects.create(order=order, product_id=order.product_id, quantity=order.quantity)
            updated = Product.objects.filter(pk=order.product_id, stock__gte=order.quantity).update(
                stock=F("stock") - order.quantity
            )
            if updated == 0:
                # atomic() rolls the Reservation row back too
                raise ProductUnavailable(f"not enough stock for {order.quantity} item(s)")
    except IntegrityError:
        return False  # this order already has a reservation
    return True


def charge_payment(order):
    """
    Step 6. Simulated payment gateway. Returns (payment, created_now).
    One Payment row per order: a retry after a lost response finds the old row
    instead of charging the customer twice.
    """
    return Payment.objects.get_or_create(order=order, defaults={"amount": order.total_amount})


def set_status(order_id, status, reason=""):
    """Steps 7 and 9. Absolute update (not 'add one'), so repeating it is harmless."""
    return Order.objects.filter(pk=order_id).update(
        status=status, failure_reason=reason, updated_at=timezone.now()
    )


# ------------------------------------------------------------ compensation
def release_inventory(order):
    """Undo reserve_inventory. Returns True if stock was given back, False if nothing to undo."""
    with transaction.atomic():
        reservation = Reservation.objects.select_for_update().filter(order=order, released=False).first()
        if reservation is None:
            return False
        Product.objects.filter(pk=reservation.product_id).update(stock=F("stock") + reservation.quantity)
        reservation.released = True
        reservation.save(update_fields=["released"])
    return True


def refund_payment(order):
    """Undo charge_payment. Returns True if a refund happened now, False if nothing to refund."""
    return Payment.objects.filter(order=order, status=Payment.Status.CHARGED).update(
        status=Payment.Status.REFUNDED
    ) == 1


# ------------------------------------------------------- notifications / reports
def record_notification(workflow_id, recipient, subject):
    """Database update used by the notification workflow. Idempotent by workflow_id."""
    log, _ = NotificationLog.objects.update_or_create(
        workflow_id=workflow_id, defaults={"recipient": recipient, "subject": subject}
    )
    return log


def build_report():
    """Numbers for the recurring report: orders and revenue per status."""
    rows = Order.objects.values("status").annotate(orders=Count("id"), revenue=Sum("total_amount"))
    return {
        row["status"]: {"orders": row["orders"], "revenue": f"{Decimal(row['revenue'] or 0):.2f}"}
        for row in rows
    }
