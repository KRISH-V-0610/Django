"""
Activities of the order-processing workflow (assignment 4.4).

Rules used here:
  * Business errors (bad order, no stock, card declined) -> ApplicationError(non_retryable=True).
    Retrying cannot fix them, so Temporal must not retry them.
  * Temporary errors (gateway down, SMTP down) -> normal exceptions: Temporal retries them
    with the retry policy chosen by the workflow.
  * Every side effect is idempotent (see orders/services.py): a retry can never double charge
    or double reserve.
"""
import time

from django.conf import settings
from django.core.mail import send_mail
from temporalio import activity
from temporalio.exceptions import ApplicationError

from orders import services
from orders.models import Order, Simulate
from temporal_app.constants import PAYMENT_API_DOWN_ATTEMPTS, PAYMENT_START_TO_CLOSE
from temporal_app.payloads import ValidationResult


def _load_order(order_id: str) -> Order:
    try:
        return Order.objects.select_related("product").get(pk=order_id)
    except Order.DoesNotExist:
        raise ApplicationError(
            f"order {order_id} does not exist", type="OrderNotFound", non_retryable=True
        )


# ------------------------------------------------------------ forward steps
@activity.defn
def validate_order(order_id: str) -> ValidationResult:
    """Step 3: is the order itself valid? Also decides if a manual approval is needed."""
    order = _load_order(order_id)
    try:
        requires_approval = services.validate_order(order)
    except services.InvalidOrder as exc:
        raise ApplicationError(str(exc), type="InvalidOrder", non_retryable=True)
    return ValidationResult(
        requires_approval=requires_approval,
        customer_email=order.customer_email,
        total=str(order.total_amount),
    )


@activity.defn
def check_availability(order_id: str) -> None:
    """Step 4: is there enough stock right now? (read only)"""
    try:
        services.check_availability(_load_order(order_id))
    except services.ProductUnavailable as exc:
        raise ApplicationError(str(exc), type="ProductUnavailable", non_retryable=True)


@activity.defn
def reserve_inventory(order_id: str) -> bool:
    """Step 5: take the stock. Returns False if an earlier attempt already reserved it."""
    try:
        return services.reserve_inventory(_load_order(order_id))
    except services.ProductUnavailable as exc:
        raise ApplicationError(str(exc), type="ProductUnavailable", non_retryable=True)


@activity.defn
def charge_payment(order_id: str) -> int:
    """
    Step 6: simulated payment gateway. The `simulate` field of the order picks the scenario:
      payment_declined -> business error, never retried
      payment_api_down -> the gateway fails on attempts 1 and 2, works on attempt 3 (retried)
      payment_timeout  -> attempt 1 charges, then "loses the response" and exceeds the timeout;
                          the retry must NOT charge again (idempotency)
    """
    order = _load_order(order_id)
    attempt = activity.info().attempt

    if order.simulate == Simulate.PAYMENT_DECLINED:
        raise ApplicationError("card was declined", type="PaymentDeclined", non_retryable=True)
    if order.simulate == Simulate.PAYMENT_API_DOWN and attempt <= PAYMENT_API_DOWN_ATTEMPTS:
        raise ApplicationError(
            f"payment gateway unavailable (attempt {attempt})", type="GatewayUnavailable"
        )

    payment, created = services.charge_payment(order)
    activity.logger.info("payment %s for order %s (new charge: %s)", payment.pk, order_id, created)

    if order.simulate == Simulate.PAYMENT_TIMEOUT and attempt == 1:
        time.sleep(PAYMENT_START_TO_CLOSE.total_seconds() + 2)  # money taken, answer too late
    return payment.pk


@activity.defn
def update_order_status(order_id: str, status: str, reason: str = "") -> None:
    """Steps 7 and 9 (and failure/cancel): database update."""
    services.set_status(order_id, status, reason)


@activity.defn
def send_order_confirmation(order_id: str, to: str) -> str:
    """Step 8: confirmation email. Runs on its own task queue (email-queue)."""
    order = _load_order(order_id)
    if order.simulate == Simulate.EMAIL_DOWN:
        raise ConnectionError("SMTP server unavailable (simulated)")
    send_mail(
        f"Order {order_id} confirmed",
        f"Thanks! We received your order for {order.quantity} x {order.product.name} "
        f"(total {order.total_amount}).",
        settings.DEFAULT_FROM_EMAIL,
        [to],
    )
    return f"confirmation sent to {to}"


# ------------------------------------------------------------- compensation
@activity.defn
def release_inventory(order_id: str) -> bool:
    """Compensation for reserve_inventory. Harmless if nothing was reserved."""
    return services.release_inventory(_load_order(order_id))


@activity.defn
def refund_payment(order_id: str) -> bool:
    """Compensation for charge_payment. Harmless if nothing was charged."""
    return services.refund_payment(_load_order(order_id))
