"""
Order processing workflow (assignment 4.4, topics C, G and H).

    validate -> check availability -> [wait for approval] -> reserve inventory
             -> payment -> update status -> confirmation email -> complete

Failure handling ("saga"):
  * a business error (no stock, card declined) is not retried; every step that already
    succeeded is undone in reverse order (refund payment, release inventory) = compensation
  * a temporary error (gateway down, timeout) is retried by the activity retry policy
  * an email that keeps failing does not undo the order, it is logged and the order completes
  * a "cancel" signal while the order is being processed also triggers the compensation

State (status, progress, approval...) lives in this object. Temporal rebuilds it by
replaying the event history, so it survives worker crashes and restarts.
"""
import asyncio

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.exceptions import CancelledError as TemporalCancelledError

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities.orders import (
        charge_payment,
        check_availability,
        refund_payment,
        release_inventory,
        reserve_inventory,
        send_order_confirmation,
        update_order_status,
        validate_order,
    )
    from temporal_app.constants import (
        APPROVAL_TIMEOUT,
        DEFAULT_START_TO_CLOSE,
        EMAIL_START_TO_CLOSE,
        EMAIL_TASK_QUEUE,
        PAYMENT_START_TO_CLOSE,
    )
    from temporal_app.payloads import OrderResult
    from temporal_app.policies import DB_RETRY, EMAIL_RETRY, EXTERNAL_RETRY

TOTAL_STEPS = 7  # validate, availability, reserve, payment, status, email, complete


class _OrderCancelled(Exception):
    """Raised inside the workflow when a cancel request is found. Always caught in run()."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _describe(exc: ActivityError) -> str:
    cause = exc.cause
    if isinstance(cause, ApplicationError):
        return f"{cause.type}: {cause.message}"
    return f"{type(cause).__name__}: {cause}"


@workflow.defn
class OrderWorkflow:
    def __init__(self) -> None:
        self._order_id = ""
        self._status = "STARTED"
        self._reason = ""
        self._completed: list[str] = []
        self._approved = False
        self._cancel_reason: str | None = None
        self._email: str | None = None
        # set BEFORE the activity runs: if we get cancelled or time out half way, the
        # compensation still runs (and is harmless if the step never happened)
        self._reserved = False
        self._charged = False

    # ------------------------------------------------------------------ signals
    @workflow.signal
    def approve(self) -> None:
        self._approved = True

    @workflow.signal
    def cancel(self, reason: str = "cancelled by customer") -> None:
        if self._cancel_reason is None:
            self._cancel_reason = reason

    @workflow.signal
    def update_email(self, email: str) -> None:
        self._email = email

    # ------------------------------------------------------------------ queries
    @workflow.query
    def status(self) -> dict:
        return {
            "order_id": self._order_id,
            "status": self._status,
            "approved": self._approved,
            "cancel_requested": self._cancel_reason is not None,
            "confirmation_email": self._email,
        }

    @workflow.query
    def progress(self) -> dict:
        done = len(self._completed)
        return {
            "completed_steps": list(self._completed),
            "total_steps": TOTAL_STEPS,
            "percent": round(100 * done / TOTAL_STEPS),
        }

    # --------------------------------------------------------------------- main
    @workflow.run
    async def run(self, order_id: str) -> OrderResult:
        self._order_id = order_id
        email_sent = False
        try:
            email_sent = await self._process()
            note = "" if email_sent else "confirmation email could not be sent"
            await self._finish("COMPLETED", note)
        except _OrderCancelled as exc:
            await self._compensate_and_finish("CANCELLED", exc.reason)
        except ActivityError as exc:
            if isinstance(exc.cause, TemporalCancelledError):
                await self._compensate_and_finish("CANCELLED", "workflow cancelled")
                raise
            reason = _describe(exc)
            workflow.logger.error("order %s failed: %s", order_id, reason)
            await self._compensate_and_finish("FAILED", reason)
        except asyncio.CancelledError:
            # the whole workflow was cancelled from outside (handle.cancel())
            await self._compensate_and_finish("CANCELLED", "workflow cancelled")
            raise
        return OrderResult(
            order_id=order_id,
            status=self._status,
            reason=self._reason,
            email_sent=email_sent,
            steps=list(self._completed),
        )

    async def _process(self) -> bool:
        """The happy path. Returns True if the confirmation email was sent."""
        order_id = self._order_id

        # 3. validate
        self._status = "VALIDATING"
        validation = await self._call(validate_order, order_id, retry_policy=DB_RETRY)
        self._done("validate")
        self._check_cancel()

        # 4. product availability (non-retryable ProductUnavailable if too low)
        self._status = "CHECKING_AVAILABILITY"
        await self._call(check_availability, order_id, retry_policy=DB_RETRY)
        self._done("check_availability")
        self._check_cancel()

        # large orders wait here until someone sends the "approve" signal
        if validation.requires_approval:
            self._status = "AWAITING_APPROVAL"
            await self._call(update_order_status, order_id, "AWAITING_APPROVAL", retry_policy=DB_RETRY)
            try:
                await workflow.wait_condition(
                    lambda: self._approved or self._cancel_reason is not None,
                    timeout=APPROVAL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise _OrderCancelled("approval timed out")
            self._check_cancel()

        # 5. reserve inventory
        self._status = "RESERVING_INVENTORY"
        self._reserved = True
        await self._call(reserve_inventory, order_id, retry_policy=DB_RETRY)
        self._done("reserve_inventory")
        self._check_cancel()

        # 6. simulated payment (temporary errors retried, declined card is not)
        self._status = "PROCESSING_PAYMENT"
        self._charged = True
        await self._call(
            charge_payment,
            order_id,
            start_to_close_timeout=PAYMENT_START_TO_CLOSE,
            retry_policy=EXTERNAL_RETRY,
        )
        self._done("payment")
        self._check_cancel()

        # 7. update order status
        await self._call(update_order_status, order_id, "CONFIRMED", retry_policy=DB_RETRY)
        self._status = "CONFIRMED"
        self._done("update_status")
        self._check_cancel()

        # 8. email. The signal "update_email" may have changed the address in the meantime.
        self._status = "SENDING_EMAIL"
        email_sent = await self._send_email(validation.customer_email)
        self._done("email")
        return email_sent

    # ------------------------------------------------------------------ helpers
    def _call(self, activity_fn, *args, **kwargs):
        kwargs.setdefault("start_to_close_timeout", DEFAULT_START_TO_CLOSE)
        return workflow.execute_activity(activity_fn, args=list(args), **kwargs)

    def _done(self, step: str) -> None:
        self._completed.append(step)

    def _check_cancel(self) -> None:
        if self._cancel_reason is not None:
            raise _OrderCancelled(self._cancel_reason)

    async def _send_email(self, default_address: str) -> bool:
        address = self._email or default_address
        try:
            await self._call(
                send_order_confirmation,
                self._order_id,
                address,
                task_queue=EMAIL_TASK_QUEUE,
                start_to_close_timeout=EMAIL_START_TO_CLOSE,
                retry_policy=EMAIL_RETRY,
            )
        except ActivityError as exc:
            # not worth undoing a paid order for: log it and carry on
            workflow.logger.error("order %s: email failed: %s", self._order_id, _describe(exc))
            return False
        return True

    async def _finish(self, status: str, reason: str = "") -> None:
        await self._call(update_order_status, self._order_id, status, reason, retry_policy=DB_RETRY)
        self._status = status
        self._reason = reason
        if status == "COMPLETED":
            self._done("complete")

    async def _compensate_and_finish(self, status: str, reason: str) -> None:
        """Undo what was done, newest first, then store the final status."""
        self._status = "COMPENSATING"
        if self._charged:
            await self._call(refund_payment, self._order_id, retry_policy=DB_RETRY)
        if self._reserved:
            await self._call(release_inventory, self._order_id, retry_policy=DB_RETRY)
        await self._finish(status, reason)
