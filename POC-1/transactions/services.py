import logging

from django.db import transaction

from orders.models import Order

from .models import Product, TransactionRecord

logger = logging.getLogger(__name__)


# ---- errors the API turns into proper HTTP responses -------------------------

class OrderError(Exception):
    status_code = 400
    message = "Order failed"

    def __init__(self, message=None):
        super().__init__(message or self.message)
        self.message = message or self.message


class ProductNotFound(OrderError):
    status_code = 404
    message = "Product not found"


class NotEnoughStock(OrderError):
    status_code = 400
    message = "Not enough stock"


class SimulatedFailure(OrderError):
    status_code = 500
    message = "Simulated failure - everything was rolled back"


# ---- side effect that must only run after a real commit ----------------------

def send_confirmation(order_id):
    # In real life: send an email / publish an event / call a payment API.
    logger.warning("[on_commit] confirmation sent for order %s", order_id)


# ---- the workflow ------------------------------------------------------------

def place_order(product_id, quantity, fail_at=None):
    """
    All database steps happen inside ONE transaction.
    If any step raises, Django rolls back every step (no half-finished order).

    fail_at lets us simulate a crash:
      "after_stock"  -> crash after stock was deducted      (full rollback)
      "after_record" -> crash after the receipt was created (full rollback)
      "free_sample"  -> only the optional nested step fails (savepoint rollback)
    """
    with transaction.atomic():

        # lock the product row so two buyers can't both take the last item
        # selct_for_update() is a pessimistic lock - it blocks other transactions until this one commits or rolls back.
        
        product = Product.objects.select_for_update().filter(id=product_id).first()
        if product is None:
            raise ProductNotFound()

        # 1. create the order
        order = Order.objects.create(
            product_name=product.name,
            amount=product.price * quantity,
            product=product,
            quantity=quantity,
            status=Order.Status.PENDING,
        )

        # 2. deduct inventory
        if product.stock < quantity:
            raise NotEnoughStock()
        product.stock -= quantity
        product.save(update_fields=["stock"])
        
        # --> simulate a crash after stock was deducted, but before the transaction record was created
        if fail_at == "after_stock":
            raise SimulatedFailure()

        # 3. create the transaction record (receipt)
        TransactionRecord.objects.create(
            order=order, product=product, quantity=quantity, amount=order.amount
        )
        if fail_at == "after_record":
            raise SimulatedFailure()

        # 4. optional extra step in a NESTED atomic block = a savepoint.
        #    If it fails we catch it OUTSIDE the nested block, so only this
        #    step is undone and the main order carries on.
        free_sample = False
        try:
            with transaction.atomic():
                if product.stock < 1:
                    raise NotEnoughStock("No stock left for a free sample")
                product.stock -= 1
                product.save(update_fields=["stock"])
                if fail_at == "free_sample":
                    raise SimulatedFailure()
                free_sample = True
        except OrderError:
            # the nested block rolled back to the savepoint, but our in-memory
            # product still shows the lower stock - reload the real value.
            product.refresh_from_db()

        # 5. update the order status
        order.status = Order.Status.CONFIRMED
        order.save(update_fields=["status"])

        # 6. runs ONLY if the outer transaction commits (skipped on rollback)
        transaction.on_commit(lambda: send_confirmation(order.id))

    return {
        "order_id": order.id,
        "status": order.status,
        "product_id": product.id,
        "quantity": quantity,
        "amount": str(order.amount),
        "stock_left": product.stock,
        "free_sample": free_sample,
    }
