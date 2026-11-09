"""
Assignment 4.4: every order scenario, started straight from Python (no HTTP involved).

    terminal 1:  uv run python -m temporal_app.workers.run_worker            (all queues)
    terminal 2:  uv run python -m demos.orders success
                 uv run python -m demos.orders all

Each run first resets the demo products (seed_products), so the stock numbers are comparable.
Scenarios: success, large, large_cancel, unavailable, declined, api_down, timeout, email_down,
cancel, duplicate.
"""
import asyncio
import io
import sys

from asgiref.sync import sync_to_async
from django.core.management import call_command
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import WorkflowAlreadyStartedError

from demos.common import get_client, title
from orders import services
from orders.models import Order, Payment, Product, Reservation
from temporal_app.constants import ORDER_EXECUTION_TIMEOUT, ORDERS_TASK_QUEUE, order_workflow_id
from temporal_app.workflows.order import OrderWorkflow

# sku, quantity, simulate, what happens to the running workflow, explanation
SCENARIOS = {
    "success": ("WIDGET", 2, "", None, "happy path: all steps, order COMPLETED"),
    "large": ("PRICEY", 1, "", "approve", "total >= 1000: waits for the approve signal, then completes"),
    "large_cancel": ("PRICEY", 1, "", "cancel_waiting", "cancel signal while waiting for approval"),
    "unavailable": ("GADGET", 5, "", None, "only 2 in stock: non-retryable, order FAILED, nothing to undo"),
    "declined": ("WIDGET", 1, "payment_declined", None, "card declined: non-retryable, stock is released again"),
    "api_down": ("WIDGET", 1, "payment_api_down", None, "gateway fails twice, retries with backoff, then works"),
    "timeout": ("WIDGET", 1, "payment_timeout", None, "attempt 1 charges but times out; the retry must not charge twice"),
    "email_down": ("WIDGET", 1, "email_down", None, "email fails 3 times; the order still completes, the failure is logged"),
    "cancel": ("WIDGET", 1, "payment_timeout", "cancel_processing", "cancel while processing: refund + release stock"),
    "duplicate": ("WIDGET", 1, "", "duplicate", "a second start with the same workflow id is refused"),
}


@sync_to_async
def reset_products() -> None:
    call_command("seed_products", stdout=io.StringIO())


@sync_to_async
def create_order(sku: str, quantity: int, simulate: str) -> Order:
    return services.create_order(
        sku=sku, quantity=quantity, customer_email="customer@example.com", simulate=simulate
    )


@sync_to_async
def snapshot(order_id, sku: str) -> dict:
    order = Order.objects.get(pk=order_id)
    payment = Payment.objects.filter(order=order).first()
    reservation = Reservation.objects.filter(order=order).first()
    return {
        "db_status": order.status,
        "failure_reason": order.failure_reason,
        "stock_now": Product.objects.get(sku=sku).stock,
        "payments": Payment.objects.filter(order=order).count(),
        "payment_status": payment.status if payment else None,
        "reservation_released": reservation.released if reservation else None,
    }


async def wait_for_status(handle, wanted: str, seconds: int = 20) -> bool:
    for _ in range(seconds * 2):
        state = await handle.query(OrderWorkflow.status)
        if state["status"] == wanted:
            return True
        await asyncio.sleep(0.5)
    return False


async def drive(handle, action: str | None) -> None:
    """Send the signals (and extra start) the scenario needs while the workflow runs."""
    if action == "approve":
        if await wait_for_status(handle, "AWAITING_APPROVAL"):
            print("  workflow is waiting for approval -> sending the approve signal")
            await handle.signal(OrderWorkflow.approve)
    elif action == "cancel_waiting":
        if await wait_for_status(handle, "AWAITING_APPROVAL"):
            print("  workflow is waiting for approval -> sending the cancel signal")
            await handle.signal(OrderWorkflow.cancel, "customer changed their mind")
    elif action == "cancel_processing":
        await asyncio.sleep(2)  # the slow first payment attempt is still running
        print("  payment in progress -> sending the cancel signal")
        await handle.signal(OrderWorkflow.cancel, "customer cancelled during payment")


async def run_scenario(client, name: str) -> None:
    sku, quantity, simulate, action, explanation = SCENARIOS[name]
    title(f"{name}: {explanation}")
    await reset_products()
    order = await create_order(sku, quantity, simulate)
    print(f"  order {order.id}: {quantity} x {sku}, total {order.total_amount}, simulate={simulate or '-'}")

    workflow_id = order_workflow_id(order.id)
    handle = await client.start_workflow(
        OrderWorkflow.run,
        str(order.id),
        id=workflow_id,
        task_queue=ORDERS_TASK_QUEUE,
        execution_timeout=ORDER_EXECUTION_TIMEOUT,
    )

    driver = None
    if action == "duplicate":
        try:
            await client.start_workflow(
                OrderWorkflow.run, str(order.id), id=workflow_id, task_queue=ORDERS_TASK_QUEUE
            )
            print("  !! second start was accepted (unexpected)")
        except WorkflowAlreadyStartedError:
            print("  second start with the same workflow id was refused by Temporal (good)")
    else:
        driver = asyncio.ensure_future(drive(handle, action))

    try:
        result = await asyncio.wait_for(handle.result(), timeout=120)
        print(f"  workflow result: {result}")
    except WorkflowFailureError as exc:
        print(f"  workflow ended with: {type(exc.cause).__name__}")
    finally:
        if driver:
            await driver
    print("  database now   :", await snapshot(order.id, sku))
    queries = await handle.query(OrderWorkflow.progress)
    print("  progress query :", queries)


async def main(names: list[str]) -> None:
    client = await get_client()
    for name in names:
        await run_scenario(client, name)


if __name__ == "__main__":
    wanted = sys.argv[1:]
    if wanted == ["all"]:
        wanted = list(SCENARIOS)
    unknown = [name for name in wanted if name not in SCENARIOS]
    if not wanted or unknown:
        print("usage: python -m demos.orders <scenario ...> | all")
        for key, (_, _, _, _, text) in SCENARIOS.items():
            print(f"  {key:13} {text}")
        raise SystemExit(1 if unknown else 0)
    asyncio.run(main(wanted))
