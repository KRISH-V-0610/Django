"""
OrderWorkflow tested end to end on Temporal's time-skipping test server.

The real activities touch the database, so here they are replaced by MOCK activities
registered under the SAME NAMES (validate_order, charge_payment, ...). The workflow cannot
tell the difference. The mocks record every call, so the tests can check the exact order
of steps and of compensation.
"""
import asyncio
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.worker import Worker

from temporal_app.constants import EMAIL_TASK_QUEUE
from temporal_app.payloads import ValidationResult
from temporal_app.workflows.order import OrderWorkflow
from tests.helpers import wait_for_status, wait_until

FULL_HAPPY_PATH = [
    "validate", "check_availability", "reserve_inventory", "payment", "update_status", "email", "complete",
]


@dataclass
class Backend:
    """Switches for the fake activities, plus a record of what happened."""

    requires_approval: bool = False
    unavailable: bool = False
    declined: bool = False
    payment_fail_attempts: int = 0  # gateway error on the first N attempts
    payment_hangs_on_first_attempt: bool = False  # never answers: start-to-close timeout must fire
    payment_gate: asyncio.Event | None = None  # payment waits here until the test opens the gate
    email_down: bool = False

    calls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    reserved: bool = False
    charged: bool = False


def make_activities(b: Backend):
    """Returns (activities for the orders queue, activities for the email queue)."""

    @activity.defn(name="validate_order")
    async def validate_order(order_id: str) -> ValidationResult:
        b.calls.append("validate_order")
        return ValidationResult(
            requires_approval=b.requires_approval, customer_email="customer@example.com", total="100.00"
        )

    @activity.defn(name="check_availability")
    async def check_availability(order_id: str) -> None:
        b.calls.append("check_availability")
        if b.unavailable:
            raise ApplicationError("only 1 left", type="ProductUnavailable", non_retryable=True)

    @activity.defn(name="reserve_inventory")
    async def reserve_inventory(order_id: str) -> bool:
        b.calls.append("reserve_inventory")
        b.reserved = True
        return True

    @activity.defn(name="charge_payment")
    async def charge_payment(order_id: str) -> int:
        attempt = activity.info().attempt
        b.calls.append(f"charge_payment#{attempt}")
        if b.payment_gate is not None:
            await b.payment_gate.wait()
        if b.payment_hangs_on_first_attempt and attempt == 1:
            await asyncio.sleep(3600)
        if b.declined:
            raise ApplicationError("card declined", type="PaymentDeclined", non_retryable=True)
        if attempt <= b.payment_fail_attempts:
            raise ApplicationError("gateway down", type="GatewayUnavailable")
        b.charged = True
        return 1

    @activity.defn(name="update_order_status")
    async def update_order_status(order_id: str, status: str, reason: str = "") -> None:
        b.calls.append(f"update_order_status:{status}")

    @activity.defn(name="release_inventory")
    async def release_inventory(order_id: str) -> bool:
        b.calls.append("release_inventory")
        b.reserved = False
        return True

    @activity.defn(name="refund_payment")
    async def refund_payment(order_id: str) -> bool:
        b.calls.append("refund_payment")
        b.charged = False
        return True

    @activity.defn(name="send_order_confirmation")
    async def send_order_confirmation(order_id: str, to: str) -> str:
        b.calls.append(f"send_order_confirmation#{activity.info().attempt}")
        if b.email_down:
            raise ConnectionError("SMTP down")
        b.emails.append(to)
        return f"sent to {to}"

    orders = [
        validate_order, check_availability, reserve_inventory, charge_payment,
        update_order_status, release_inventory, refund_payment,
    ]
    return orders, [send_order_confirmation]


class Harness:
    """Starts the two workers (orders queue + email queue) and the workflow."""

    def __init__(self, env, task_queue, workflow_id, backend):
        self.env, self.task_queue, self.workflow_id, self.backend = env, task_queue, workflow_id, backend

    async def __aenter__(self):
        order_activities, email_activities = make_activities(self.backend)
        self._workers = [
            Worker(self.env.client, task_queue=self.task_queue, workflows=[OrderWorkflow], activities=order_activities),
            Worker(self.env.client, task_queue=EMAIL_TASK_QUEUE, activities=email_activities),
        ]
        for worker in self._workers:
            await worker.__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        for worker in reversed(self._workers):
            await worker.__aexit__(*exc_info)

    async def start(self):
        return await self.env.client.start_workflow(
            OrderWorkflow.run, "order-1", id=self.workflow_id, task_queue=self.task_queue
        )

    async def run(self):
        return await self.env.client.execute_workflow(
            OrderWorkflow.run, "order-1", id=self.workflow_id, task_queue=self.task_queue
        )


@pytest.fixture
def harness(env, task_queue, workflow_id):
    def make(**backend_settings):
        return Harness(env, task_queue, workflow_id, Backend(**backend_settings))

    return make


# ------------------------------------------------------------------ happy path
async def test_success_runs_every_step_in_order(harness):
    async with harness() as h:
        result = await h.run()

    assert result.status == "COMPLETED"
    assert result.email_sent is True
    assert result.steps == FULL_HAPPY_PATH
    assert h.backend.calls == [
        "validate_order", "check_availability", "reserve_inventory", "charge_payment#1",
        "update_order_status:CONFIRMED", "send_order_confirmation#1", "update_order_status:COMPLETED",
    ]
    assert h.backend.emails == ["customer@example.com"]


# ------------------------------------------------------------------ business failures
async def test_product_unavailable_fails_and_has_nothing_to_undo(harness):
    async with harness(unavailable=True) as h:
        result = await h.run()

    assert result.status == "FAILED"
    assert "ProductUnavailable" in result.reason
    assert "reserve_inventory" not in h.backend.calls  # never got that far
    assert "release_inventory" not in h.backend.calls  # so nothing to compensate
    assert h.backend.calls[-1] == "update_order_status:FAILED"
    assert h.backend.emails == []


async def test_payment_declined_is_not_retried_and_is_compensated(harness):
    async with harness(declined=True) as h:
        result = await h.run()

    assert result.status == "FAILED"
    assert "PaymentDeclined" in result.reason
    assert h.backend.calls.count("charge_payment#1") == 1
    assert "charge_payment#2" not in h.backend.calls  # non-retryable: one attempt only
    # compensation: newest step first, then the final status
    assert h.backend.calls[-3:] == ["refund_payment", "release_inventory", "update_order_status:FAILED"]
    assert h.backend.reserved is False and h.backend.charged is False
    assert h.backend.emails == []


# ------------------------------------------------------------------ retries and timeouts
async def test_gateway_down_is_retried_until_it_works(harness):
    async with harness(payment_fail_attempts=2) as h:
        result = await h.run()

    assert result.status == "COMPLETED"
    assert [c for c in h.backend.calls if c.startswith("charge_payment")] == [
        "charge_payment#1", "charge_payment#2", "charge_payment#3",
    ]
    assert "refund_payment" not in h.backend.calls  # a retried error is not a failure


async def test_payment_that_never_answers_hits_the_timeout_and_is_retried(harness):
    async with harness(payment_hangs_on_first_attempt=True) as h:
        result = await h.run()

    assert result.status == "COMPLETED"
    assert [c for c in h.backend.calls if c.startswith("charge_payment")] == ["charge_payment#1", "charge_payment#2"]


async def test_email_failure_is_retried_then_the_order_still_completes(harness):
    async with harness(email_down=True) as h:
        result = await h.run()

    assert result.status == "COMPLETED"
    assert result.email_sent is False
    assert result.reason == "confirmation email could not be sent"
    assert h.backend.calls.count("send_order_confirmation#1") == 1
    assert [c for c in h.backend.calls if c.startswith("send_order_confirmation")] == [
        "send_order_confirmation#1", "send_order_confirmation#2", "send_order_confirmation#3",
    ]  # EMAIL_RETRY = 3 attempts
    assert "refund_payment" not in h.backend.calls  # a paid order is not undone for a lost email


# ------------------------------------------------------------------ signals and queries
async def test_large_order_waits_for_approval_signal(harness):
    async with harness(requires_approval=True) as h:
        handle = await h.start()
        await wait_for_status(handle, OrderWorkflow.status, "AWAITING_APPROVAL")

        # queries read the live state without changing anything
        state = await handle.query(OrderWorkflow.status)
        progress = await handle.query(OrderWorkflow.progress)
        assert state["approved"] is False
        assert progress["completed_steps"] == ["validate", "check_availability"]
        assert progress["percent"] == 29  # 2 of 7 steps
        assert "reserve_inventory" not in h.backend.calls  # nothing is reserved before approval

        await handle.signal(OrderWorkflow.approve)
        result = await handle.result()

        assert result.status == "COMPLETED"
        # queries still work after the workflow has finished (answered from its history)
        assert (await handle.query(OrderWorkflow.progress))["percent"] == 100
        assert (await handle.query(OrderWorkflow.status))["status"] == "COMPLETED"


async def test_cancel_signal_while_waiting_for_approval(harness):
    async with harness(requires_approval=True) as h:
        handle = await h.start()
        await wait_for_status(handle, OrderWorkflow.status, "AWAITING_APPROVAL")

        await handle.signal(OrderWorkflow.cancel, "customer changed their mind")
        result = await handle.result()

    assert result.status == "CANCELLED"
    assert result.reason == "customer changed their mind"
    assert "reserve_inventory" not in h.backend.calls
    assert "refund_payment" not in h.backend.calls
    assert h.backend.calls[-1] == "update_order_status:CANCELLED"


async def test_cancel_signal_during_payment_refunds_and_releases_stock(harness):
    gate = asyncio.Event()
    async with harness(payment_gate=gate) as h:
        handle = await h.start()
        await wait_until(lambda: "charge_payment#1" in h.backend.calls)  # payment is in progress

        await handle.signal(OrderWorkflow.cancel, "changed my mind")
        gate.set()  # let the payment finish: the money IS taken, so it must be given back
        result = await handle.result()

    assert result.status == "CANCELLED"
    assert result.reason == "changed my mind"
    assert h.backend.calls[-3:] == ["refund_payment", "release_inventory", "update_order_status:CANCELLED"]
    assert h.backend.charged is False and h.backend.reserved is False
    assert h.backend.emails == []


async def test_update_email_signal_changes_the_confirmation_address(harness):
    async with harness(requires_approval=True) as h:
        handle = await h.start()
        await wait_for_status(handle, OrderWorkflow.status, "AWAITING_APPROVAL")

        await handle.signal(OrderWorkflow.update_email, "new-address@example.com")
        await handle.signal(OrderWorkflow.approve)
        result = await handle.result()

    assert result.status == "COMPLETED"
    assert h.backend.emails == ["new-address@example.com"]


async def test_approval_timeout_cancels_the_order(harness):
    """Nobody approves for 24 hours. Time skipping makes that take milliseconds."""
    async with harness(requires_approval=True) as h:
        result = await h.run()

    assert result.status == "CANCELLED"
    assert result.reason == "approval timed out"


# ------------------------------------------------------------------ native workflow cancellation
async def test_workflow_cancel_while_waiting_for_approval(harness):
    """handle.cancel() (Temporal's own cancellation, not our signal)."""
    async with harness(requires_approval=True) as h:
        handle = await h.start()
        await wait_for_status(handle, OrderWorkflow.status, "AWAITING_APPROVAL")

        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as caught:
            await handle.result()

    assert isinstance(caught.value.cause, CancelledError)
    assert h.backend.calls[-1] == "update_order_status:CANCELLED"  # cleanup still ran


async def test_workflow_cancel_during_payment_still_compensates(harness):
    gate = asyncio.Event()
    async with harness(payment_gate=gate) as h:
        handle = await h.start()
        await wait_until(lambda: "charge_payment#1" in h.backend.calls)

        await handle.cancel()
        try:
            with pytest.raises(WorkflowFailureError) as caught:
                await handle.result()
        finally:
            gate.set()  # free the stuck mock activity

    assert isinstance(caught.value.cause, CancelledError)
    assert h.backend.calls[-3:] == ["refund_payment", "release_inventory", "update_order_status:CANCELLED"]
