"""
Demo workflows for topics B, C, F, G.

WORKFLOW RULES (the sandbox enforces most of them):
  * Deterministic: replaying the same history must make the same decisions. So no
    random numbers, no datetime.now(), no sleeping with time.sleep, no network, no files,
    no database. Use workflow.now(), workflow.uuid4(), workflow.sleep() and activities.
  * Everything with a side effect goes into an Activity.
"""
import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities.demo import (
        call_external_api,
        decline_by_type,
        long_running_step,
        reject_request,
        say_hello,
        slow_call,
        stalled_call,
    )
    from temporal_app.activities.notifications import record_notification, send_email
    from temporal_app.activities.reports import generate_report
    from temporal_app.constants import (
        DEFAULT_START_TO_CLOSE,
        EMAIL_START_TO_CLOSE,
        EMAIL_TASK_QUEUE,
    )
    from temporal_app.payloads import (
        ApiCallParams,
        EmailParams,
        LongRunningParams,
        NotificationParams,
        ReportParams,
        SlowParams,
    )
    from temporal_app.policies import (
        DB_RETRY,
        EMAIL_RETRY,
        EXTERNAL_RETRY,
        NO_RETRY_FOR_BUSINESS_ERRORS,
    )


# --------------------------------------------------------------- B. simple
@workflow.defn
class HelloWorkflow:
    """The simplest workflow: run one activity and return its result."""

    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            say_hello, name, start_to_close_timeout=DEFAULT_START_TO_CLOSE
        )


# ------------------------------------- C/D/F. multi-step, several activities, 2 queues
@workflow.defn
class NotificationWorkflow:
    """
    Three activities in a row, on two different task queues:
      1. call_external_api   (default-queue)  retried with exponential backoff
      2. send_email          (email-queue)    needs a worker listening on the email queue
      3. record_notification (default-queue)  database update
    Also used by the "scheduled notification" schedule.
    """

    @workflow.run
    async def run(self, params: NotificationParams) -> dict:
        profile = await workflow.execute_activity(
            call_external_api,
            ApiCallParams("user-profile-api", params.api_fail_attempts),
            start_to_close_timeout=DEFAULT_START_TO_CLOSE,
            retry_policy=EXTERNAL_RETRY,
        )
        email = EmailParams(
            to=params.recipient,
            subject=params.subject,
            body=f"Profile lookup: {profile['data']}",
            fail_attempts=params.email_fail_attempts,
        )
        sent = await workflow.execute_activity(
            send_email,
            email,
            task_queue=EMAIL_TASK_QUEUE,  # routes this activity to the email workers
            start_to_close_timeout=EMAIL_START_TO_CLOSE,
            retry_policy=EMAIL_RETRY,
        )
        log_id = await workflow.execute_activity(
            record_notification,
            email,
            start_to_close_timeout=DEFAULT_START_TO_CLOSE,
            retry_policy=DB_RETRY,
        )
        return {"api_attempts": profile["attempt"], "email": sent, "log_id": log_id}


# --------------------------------------------------- G. retries and timeouts
@workflow.defn
class RetryDemoWorkflow:
    """
    One workflow, several failure scenarios. A failing activity does NOT fail the workflow
    by itself: the workflow gets an ActivityError and decides what to do (here: report it).

    scenarios: flaky | non_retryable | non_retryable_type | max_attempts | start_to_close |
               heartbeat | schedule_to_start
    """

    @workflow.run
    async def run(self, scenario: str) -> dict:
        try:
            result = await self._run_scenario(scenario)
        except ActivityError as exc:
            return {
                "scenario": scenario,
                "outcome": "failed",
                "error_type": self._error_type(exc.cause),
                "message": str(exc.cause),
                "retry_state": exc.retry_state.name if exc.retry_state else "",  # why Temporal stopped retrying
            }
        return {"scenario": scenario, "outcome": "succeeded", "result": result}

    @staticmethod
    def _error_type(cause) -> str:
        if isinstance(cause, ApplicationError):
            return cause.type or "ApplicationError"
        if isinstance(cause, TemporalTimeoutError):  # which of the timeouts fired?
            return f"Timeout({cause.type.name if cause.type else '?'})"
        return type(cause).__name__

    async def _run_scenario(self, scenario: str):
        if scenario == "flaky":
            # fails on attempts 1 and 2, then works. Waits 1s, 2s between attempts (backoff).
            return await workflow.execute_activity(
                call_external_api,
                ApiCallParams("flaky-api", fail_attempts=2),
                start_to_close_timeout=DEFAULT_START_TO_CLOSE,
                retry_policy=EXTERNAL_RETRY,
            )
        if scenario == "non_retryable":
            # default policy = retry forever, but the error says non_retryable, so it stops at once
            return await workflow.execute_activity(
                reject_request,
                "this order can never be accepted",
                start_to_close_timeout=DEFAULT_START_TO_CLOSE,
            )
        if scenario == "non_retryable_type":
            # the activity raises a normal error of type "PaymentDeclined"; the POLICY lists that
            # type as non-retryable, so it is not retried (same effect, decided on the workflow side)
            return await workflow.execute_activity(
                decline_by_type,
                "PaymentDeclined",
                start_to_close_timeout=DEFAULT_START_TO_CLOSE,
                retry_policy=NO_RETRY_FOR_BUSINESS_ERRORS,
            )
        if scenario == "max_attempts":
            # the API never recovers: give up after 3 attempts
            return await workflow.execute_activity(
                call_external_api,
                ApiCallParams("dead-api", fail_attempts=99),
                start_to_close_timeout=DEFAULT_START_TO_CLOSE,
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1), backoff_coefficient=2.0, maximum_attempts=3
                ),
            )
        if scenario == "start_to_close":
            # attempt 1 sleeps 10s but may only run 3s -> timeout -> retry -> attempt 2 is fast
            return await workflow.execute_activity(
                slow_call,
                SlowParams(sleep_seconds=10, bad_attempts=1),
                start_to_close_timeout=timedelta(seconds=3),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3),
            )
        if scenario == "heartbeat":
            # attempt 1 stops heartbeating after 3s -> heartbeat timeout (3s) -> retry -> attempt 2 is fine
            return await workflow.execute_activity(
                stalled_call,
                SlowParams(sleep_seconds=30, bad_attempts=1),
                start_to_close_timeout=timedelta(seconds=60),
                heartbeat_timeout=timedelta(seconds=3),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3),
            )
        if scenario == "schedule_to_start":
            # nobody listens on this queue, so the task is never picked up in time
            return await workflow.execute_activity(
                say_hello,
                "nobody",
                task_queue="queue-without-workers",
                schedule_to_start_timeout=timedelta(seconds=3),
                start_to_close_timeout=DEFAULT_START_TO_CLOSE,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        raise ApplicationError(f"unknown scenario: {scenario}", non_retryable=True)


# ------------------------------------------------- C. long running + E. restart
@workflow.defn
class LongRunningWorkflow:
    """
    Does `steps` units of work with a pause between them. Meant to run for minutes, so you can
    stop the worker in the middle, start it again, and watch it continue from the same step.
    The progress lives in the workflow history, not in the worker process.
    """

    def __init__(self) -> None:
        self._done = 0
        self._total = 0
        self._stop = False

    @workflow.signal
    def stop(self) -> None:
        self._stop = True

    @workflow.query
    def progress(self) -> dict:
        return {"done": self._done, "total": self._total, "stopped": self._stop}

    @workflow.run
    async def run(self, params: LongRunningParams) -> str:
        self._total = params.steps
        for step in range(1, params.steps + 1):
            await workflow.execute_activity(
                long_running_step, step, start_to_close_timeout=DEFAULT_START_TO_CLOSE
            )
            self._done = step
            if step == params.steps:
                break  # nothing left to do, no need to pause
            try:
                # a durable sleep that a "stop" signal can interrupt
                await workflow.wait_condition(
                    lambda: self._stop, timeout=timedelta(seconds=params.pause_seconds)
                )
            except asyncio.TimeoutError:
                pass  # pause finished normally, continue with the next step
            if self._stop:
                break  # stopped during the pause (or before it): do not start another step
        return f"finished {self._done} of {self._total} steps" + (" (stopped early)" if self._stop else "")


# -------------------------------------------------------- I. scheduled report
@workflow.defn
class ReportWorkflow:
    """Recurring report. `extra_seconds` makes it slow so schedule overlap can be demonstrated."""

    @workflow.run
    async def run(self, params: ReportParams) -> str:
        path = await workflow.execute_activity(
            generate_report, start_to_close_timeout=timedelta(seconds=30), retry_policy=DB_RETRY
        )
        if params.extra_seconds:
            await workflow.sleep(timedelta(seconds=params.extra_seconds))  # durable timer
        return path
