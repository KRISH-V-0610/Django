"""
HTTP API (Django Ninja, async views).

The API never does the order work itself. It only:
  * creates the order row and STARTS a workflow           POST /orders
  * reads the order row                                   GET  /orders/{id}
  * asks the running workflow (a QUERY)                   GET  /orders/{id}/status
  * tells the running workflow something (a SIGNAL)       POST /orders/{id}/approve | cancel | update-email

Views are `async` and share one cached Temporal client, which is bound to the event loop
it was created on. That works under uvicorn (one loop for the process) but NOT under
`runserver`/WSGI, where every request may get a fresh loop. Run it with uvicorn.
"""
import logging
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from asgiref.sync import sync_to_async
from ninja import Router, Schema, Status
from ninja.errors import HttpError
from pydantic import Field
from temporalio.client import WorkflowFailureError, WorkflowQueryFailedError
from temporalio.service import RPCError, RPCStatusCode

from temporal_app.client import get_client
from temporal_app.constants import ORDER_EXECUTION_TIMEOUT, ORDERS_TASK_QUEUE, order_workflow_id
from temporal_app.workflows.order import OrderWorkflow

from . import services
from .models import Order, Product, Simulate

logger = logging.getLogger(__name__)

router = Router(tags=["orders"])

QUERY_TIMEOUT = timedelta(seconds=5)  # a query needs a live worker, do not hang forever without one


# ------------------------------------------------------------------ schemas
class OrderIn(Schema):
    sku: str
    quantity: int = Field(gt=0)
    customer_email: str
    simulate: str = ""  # failure injection for demos/tests, see orders.models.Simulate


class EmailIn(Schema):
    email: str


class OrderOut(Schema):
    id: UUID
    workflow_id: str
    sku: str
    quantity: int
    customer_email: str
    total_amount: Decimal
    simulate: str
    status: str
    failure_reason: str


class SignalOut(Schema):
    order_id: UUID
    signal: str
    sent: bool = True


def _order_out(order: Order) -> dict:
    return {
        "id": order.id,
        "workflow_id": order.workflow_id,
        "sku": order.product.sku,
        "quantity": order.quantity,
        "customer_email": order.customer_email,
        "total_amount": order.total_amount,
        "simulate": order.simulate,
        "status": order.status,
        "failure_reason": order.failure_reason,
    }


# ------------------------------------------------------------------ helpers
async def _get_order(order_id: UUID) -> Order:
    try:
        return await Order.objects.select_related("product").aget(pk=order_id)
    except Order.DoesNotExist:
        raise HttpError(404, "order not found")


@contextmanager
def _temporal_errors():
    """Turn Temporal/network problems into clean HTTP errors instead of a 500."""
    try:
        yield
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise HttpError(409, "the order workflow is not running (it may have finished)")
        if exc.status == RPCStatusCode.DEADLINE_EXCEEDED:
            raise HttpError(503, "no worker answered in time, is a worker running?")
        logger.warning("temporal rpc error: %s", exc)
        raise HttpError(503, f"temporal error: {exc.status.name}")
    except WorkflowQueryFailedError as exc:
        raise HttpError(409, f"query failed: {exc}")
    except WorkflowFailureError as exc:
        raise HttpError(409, f"workflow failed: {exc}")
    except (RuntimeError, OSError) as exc:  # e.g. could not connect to the server
        logger.warning("cannot reach temporal: %s", exc)
        raise HttpError(503, "cannot reach the Temporal server")


async def _signal(order_id: UUID, name: str, signal_fn, *args) -> dict:
    await _get_order(order_id)  # unknown order -> 404 before we bother Temporal
    with _temporal_errors():
        client = await get_client()
        handle = client.get_workflow_handle(order_workflow_id(order_id))
        await handle.signal(signal_fn, *args)
    return {"order_id": order_id, "signal": name, "sent": True}


# ---------------------------------------------------------------- endpoints
@router.post("/orders", response={201: OrderOut})
async def create_order(request, payload: OrderIn):
    """Create the order, then start its workflow. The API answers right away (async processing)."""
    if payload.simulate not in Simulate.values:
        raise HttpError(422, f"simulate must be one of {[v for v in Simulate.values if v]}")
    try:
        order = await sync_to_async(services.create_order)(
            sku=payload.sku,
            quantity=payload.quantity,
            customer_email=payload.customer_email,
            simulate=payload.simulate,
        )
    except Product.DoesNotExist:
        raise HttpError(404, f"unknown sku {payload.sku!r}")

    try:
        with _temporal_errors():
            client = await get_client()
            await client.start_workflow(
                OrderWorkflow.run,
                str(order.id),
                id=order_workflow_id(order.id),  # business id = workflow id (no duplicates)
                task_queue=ORDERS_TASK_QUEUE,
                execution_timeout=ORDER_EXECUTION_TIMEOUT,
            )
    except HttpError:
        # no workflow will ever work on this order, do not leave it PENDING forever
        await sync_to_async(services.set_status)(order.id, Order.Status.FAILED, "could not start workflow")
        raise

    order = await _get_order(order.id)
    return Status(201, _order_out(order))


@router.get("/orders/{order_id}", response=OrderOut)
async def get_order(request, order_id: UUID):
    """The order as stored in the database (the workflow keeps it up to date)."""
    return _order_out(await _get_order(order_id))


@router.get("/orders/{order_id}/status")
async def order_status(request, order_id: UUID):
    """Live state straight from the running workflow, using two QUERIES."""
    await _get_order(order_id)
    with _temporal_errors():
        client = await get_client()
        handle = client.get_workflow_handle(order_workflow_id(order_id))
        description = await handle.describe()
        state = await handle.query(OrderWorkflow.status, rpc_timeout=QUERY_TIMEOUT)
        progress = await handle.query(OrderWorkflow.progress, rpc_timeout=QUERY_TIMEOUT)
    return {
        **state,
        "workflow_id": order_workflow_id(order_id),
        "workflow_status": description.status.name if description.status else None,
        "progress": progress,
    }


@router.post("/orders/{order_id}/approve", response=SignalOut)
async def approve_order(request, order_id: UUID):
    """SIGNAL: a manager approves a large order that is waiting."""
    return await _signal(order_id, "approve", OrderWorkflow.approve)


@router.post("/orders/{order_id}/cancel", response=SignalOut)
async def cancel_order(request, order_id: UUID, reason: str = "cancelled by customer"):
    """SIGNAL: cancel. The workflow undoes whatever it already did (release stock, refund)."""
    return await _signal(order_id, "cancel", OrderWorkflow.cancel, reason)


@router.post("/orders/{order_id}/update-email", response=SignalOut)
async def update_email(request, order_id: UUID, payload: EmailIn):
    """SIGNAL: change the address the confirmation email goes to (only matters before it is sent)."""
    return await _signal(order_id, "update_email", OrderWorkflow.update_email, payload.email)
