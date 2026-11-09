"""
Small activities used by the demo workflows (topics B, C, D, G).
They are async and never touch the database.

An Activity is where the real world is allowed: APIs, files, email, databases.
Temporal retries a failed activity; the workflow only sees the final outcome.
"""
import asyncio
import os

from temporalio import activity
from temporalio.exceptions import ApplicationError

from temporal_app.payloads import ApiCallParams, SlowParams


@activity.defn
async def say_hello(name: str) -> str:
    """The simplest possible activity. Returns the process id so multi-worker runs are visible."""
    return f"Hello {name}! (worker process {os.getpid()})"


@activity.defn
async def call_external_api(params: ApiCallParams) -> dict:
    """
    External API simulation. For the first `fail_attempts` attempts it behaves like an API
    that is temporarily down. A plain exception is a RETRYABLE failure.
    """
    attempt = activity.info().attempt
    activity.logger.info("calling %s (attempt %s)", params.name, attempt)
    await asyncio.sleep(0.3)  # pretend network time
    if attempt <= params.fail_attempts:
        raise ConnectionError(f"{params.name} is temporarily unavailable (attempt {attempt})")
    return {"api": params.name, "attempt": attempt, "data": "ok"}


@activity.defn
async def reject_request(reason: str) -> None:
    """A business error. non_retryable=True tells Temporal: do NOT retry, it will never work."""
    raise ApplicationError(reason, type="BusinessRuleViolation", non_retryable=True)


@activity.defn
async def decline_by_type(error_type: str) -> None:
    """
    A plain ApplicationError, NOT flagged non_retryable. Whether it is retried is decided by
    the retry policy of the workflow (its non_retryable_error_types list).
    """
    attempt = activity.info().attempt
    raise ApplicationError(f"{error_type} (activity attempt {attempt})", type=error_type)


@activity.defn
async def slow_call(params: SlowParams) -> dict:
    """
    Start-to-Close timeout demo: the first `bad_attempts` attempts take longer than the
    timeout set by the workflow, so Temporal gives up on them and retries.
    """
    attempt = activity.info().attempt
    if attempt <= params.bad_attempts:
        await asyncio.sleep(params.sleep_seconds)
    return {"attempt": attempt, "finished": True}


@activity.defn
async def stalled_call(params: SlowParams) -> dict:
    """
    Heartbeat timeout demo: heartbeats for 3 seconds, then goes silent (like a hung process).
    The server notices the missing heartbeat and fails the attempt long before the work "ends".
    """
    attempt = activity.info().attempt
    if attempt <= params.bad_attempts:
        for beat in range(3):
            activity.heartbeat(f"beat {beat}")
            await asyncio.sleep(1)
        await asyncio.sleep(params.sleep_seconds)  # silent: no heartbeats any more
    return {"attempt": attempt, "finished": True}


@activity.defn
async def long_running_step(step: int) -> str:
    """One unit of work for the long-running workflow."""
    activity.logger.info("long running step %s done", step)
    return f"step {step} done by process {os.getpid()}"
