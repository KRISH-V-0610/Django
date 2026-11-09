"""
Topic G: retry policy, backoff, max attempts, non-retryable errors, timeouts.

    uv run python -m temporal_app.workers.run_worker --queue default     (terminal 1)
    uv run python -m demos.retries                                       (terminal 2, all scenarios)
    uv run python -m demos.retries flaky                                 (just one)

Each scenario is explained in temporal_app/workflows/demo.py (RetryDemoWorkflow).
The retry attempts are visible in the Web UI (http://localhost:8233), "Pending Activities".
"""
import asyncio
import sys
import time

from demos.common import get_client, short_id, title
from temporal_app.constants import DEFAULT_TASK_QUEUE
from temporal_app.workflows.demo import RetryDemoWorkflow

SCENARIOS = {
    "flaky": "fails twice, 1s then 2s backoff, third attempt works",
    "non_retryable": "ApplicationError(non_retryable=True): stops at once",
    "non_retryable_type": "plain error, but its type is in the policy's non_retryable_error_types",
    "max_attempts": "never recovers: gives up after 3 attempts",
    "start_to_close": "attempt 1 exceeds the 3s start-to-close timeout, attempt 2 is fast",
    "heartbeat": "attempt 1 stops heartbeating, heartbeat timeout fires, attempt 2 works",
    "schedule_to_start": "no worker on that queue: schedule-to-start timeout after 3s",
}


async def run_one(client, scenario: str) -> None:
    title(f"{scenario}: {SCENARIOS[scenario]}")
    started = time.monotonic()
    result = await client.execute_workflow(
        RetryDemoWorkflow.run, scenario, id=f"retry-{scenario}-{short_id()}", task_queue=DEFAULT_TASK_QUEUE
    )
    print(f"took {time.monotonic() - started:.1f}s ->", result)


async def main(names: list[str]) -> None:
    client = await get_client()
    for name in names:
        await run_one(client, name)


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(SCENARIOS)
    unknown = [name for name in wanted if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenario(s) {unknown}. Choose from {list(SCENARIOS)}")
    asyncio.run(main(wanted))
