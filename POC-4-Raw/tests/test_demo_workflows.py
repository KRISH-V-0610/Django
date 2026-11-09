"""
The demo workflows (simple, multi-step, retries, long-running, child workflows) on the
time-skipping test server. Activities that need no database are the REAL ones; the ones that
need Django's database or mail are replaced by mocks with the same names.
"""
import asyncio

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.worker import Worker

from temporal_app.activities import demo, files
from temporal_app.constants import EMAIL_TASK_QUEUE, FILES_TASK_QUEUE
from temporal_app.payloads import EmailParams, LongRunningParams, NotificationParams, ReportParams
from temporal_app.workflows.demo import (
    HelloWorkflow,
    LongRunningWorkflow,
    NotificationWorkflow,
    ReportWorkflow,
    RetryDemoWorkflow,
)
from temporal_app.workflows.files import DataProcessingWorkflow, ProcessFileWorkflow


# ------------------------------------------------------------------ B. simple
async def test_hello_workflow(env, task_queue, workflow_id):
    async with Worker(env.client, task_queue=task_queue, workflows=[HelloWorkflow], activities=[demo.say_hello]):
        result = await env.client.execute_workflow(
            HelloWorkflow.run, "Krish", id=workflow_id, task_queue=task_queue
        )
    assert result.startswith("Hello Krish!")


# ------------------------------------------------------------------ multi-step on two queues
class NotificationMocks:
    """send_email and record_notification are sync + Django in real life, so they are mocked here."""

    def __init__(self):
        self.email_runs = []  # (queue the activity ran on, attempt)
        self.recorded = []

    def activities(self):
        mocks = self

        @activity.defn(name="send_email")
        async def send_email(params: EmailParams) -> str:
            info = activity.info()
            mocks.email_runs.append((info.task_queue, info.attempt))
            if info.attempt <= params.fail_attempts:
                raise ConnectionError("SMTP down")
            return f"email sent to {params.to}"

        @activity.defn(name="record_notification")
        async def record_notification(params: EmailParams) -> int:
            mocks.recorded.append((activity.info().task_queue, params.to))
            return 42

        return send_email, record_notification


async def test_notification_workflow_routes_the_email_to_its_own_queue(env, task_queue, workflow_id):
    mocks = NotificationMocks()
    send_email, record_notification = mocks.activities()
    async with Worker(
        env.client, task_queue=task_queue, workflows=[NotificationWorkflow],
        activities=[demo.call_external_api, record_notification],
    ), Worker(env.client, task_queue=EMAIL_TASK_QUEUE, activities=[send_email]):
        result = await env.client.execute_workflow(
            NotificationWorkflow.run,
            NotificationParams(recipient="a@example.com", api_fail_attempts=2),
            id=workflow_id,
            task_queue=task_queue,
        )

    assert result == {"api_attempts": 3, "email": "email sent to a@example.com", "log_id": 42}
    assert mocks.email_runs == [(EMAIL_TASK_QUEUE, 1)]  # ran on the email queue
    assert mocks.recorded == [(task_queue, "a@example.com")]  # the DB step ran on the default queue


async def test_notification_email_is_retried(env, task_queue, workflow_id):
    mocks = NotificationMocks()
    send_email, record_notification = mocks.activities()
    async with Worker(
        env.client, task_queue=task_queue, workflows=[NotificationWorkflow],
        activities=[demo.call_external_api, record_notification],
    ), Worker(env.client, task_queue=EMAIL_TASK_QUEUE, activities=[send_email]):
        await env.client.execute_workflow(
            NotificationWorkflow.run,
            NotificationParams(recipient="a@example.com", email_fail_attempts=2),
            id=workflow_id,
            task_queue=task_queue,
        )
    assert [attempt for _, attempt in mocks.email_runs] == [1, 2, 3]


# ------------------------------------------------------------------ G. retries and timeouts
@pytest.fixture
def run_scenario(env, task_queue, workflow_id):
    async def run(scenario: str) -> dict:
        # the Worker must be created inside a coroutine: it needs a running event loop
        worker = Worker(
            env.client,
            task_queue=task_queue,
            workflows=[RetryDemoWorkflow],
            activities=[
                demo.say_hello, demo.call_external_api, demo.reject_request,
                demo.decline_by_type, demo.slow_call, demo.stalled_call,
            ],
        )
        async with worker:
            return await env.client.execute_workflow(
                RetryDemoWorkflow.run, scenario, id=workflow_id, task_queue=task_queue
            )

    return run


async def test_flaky_api_succeeds_on_the_third_attempt(run_scenario):
    result = await run_scenario("flaky")
    assert result["outcome"] == "succeeded"
    assert result["result"]["attempt"] == 3


async def test_non_retryable_error_stops_at_once(run_scenario):
    result = await run_scenario("non_retryable")
    assert result["outcome"] == "failed"
    assert result["error_type"] == "BusinessRuleViolation"
    assert "NON_RETRYABLE" in result["retry_state"]


async def test_error_type_listed_in_the_policy_is_not_retried(run_scenario):
    result = await run_scenario("non_retryable_type")
    assert result["error_type"] == "PaymentDeclined"
    assert "attempt 1" in result["message"]  # the first attempt was also the last
    assert "NON_RETRYABLE" in result["retry_state"]


async def test_gives_up_after_maximum_attempts(run_scenario):
    result = await run_scenario("max_attempts")
    assert result["outcome"] == "failed"
    assert result["error_type"] == "ConnectionError"
    assert "MAXIMUM_ATTEMPTS" in result["retry_state"]


async def test_start_to_close_timeout_then_retry(run_scenario):
    result = await run_scenario("start_to_close")
    assert result["outcome"] == "succeeded"
    assert result["result"]["attempt"] == 2  # attempt 1 timed out after 3s


async def test_heartbeat_timeout_then_retry(run_scenario):
    result = await run_scenario("heartbeat")
    assert result["outcome"] == "succeeded"
    assert result["result"]["attempt"] == 2  # attempt 1 stopped heartbeating


async def test_schedule_to_start_timeout_when_no_worker_listens(run_scenario):
    result = await run_scenario("schedule_to_start")
    assert result["outcome"] == "failed"
    assert result["error_type"] == "Timeout(SCHEDULE_TO_START)"


async def test_unknown_scenario_fails_the_workflow(run_scenario):
    with pytest.raises(WorkflowFailureError):
        await run_scenario("nonsense")


# ------------------------------------------------------------------ long running (durable sleep)
async def test_long_running_workflow_finishes_all_steps(env, task_queue, workflow_id):
    async with Worker(
        env.client, task_queue=task_queue, workflows=[LongRunningWorkflow], activities=[demo.long_running_step]
    ):
        result = await env.client.execute_workflow(
            LongRunningWorkflow.run,
            LongRunningParams(steps=3, pause_seconds=3600),  # three one-hour pauses, skipped by the test server
            id=workflow_id,
            task_queue=task_queue,
        )
    assert result == "finished 3 of 3 steps"


async def test_long_running_workflow_can_be_stopped_by_signal(env, task_queue, workflow_id):
    async with Worker(
        env.client, task_queue=task_queue, workflows=[LongRunningWorkflow], activities=[demo.long_running_step]
    ):
        handle = await env.client.start_workflow(
            LongRunningWorkflow.run,
            LongRunningParams(steps=5, pause_seconds=3600),
            id=workflow_id,
            task_queue=task_queue,
        )
        for _ in range(200):  # wait (real time) until step 1 is done and the workflow is pausing
            if (await handle.query(LongRunningWorkflow.progress))["done"] >= 1:
                break
            await asyncio.sleep(0.05)
        await handle.signal(LongRunningWorkflow.stop)
        result = await handle.result()

    assert result == "finished 1 of 5 steps (stopped early)"


# ------------------------------------------------------------------ I. report with a durable timer
async def test_report_workflow_with_a_durable_sleep(env, task_queue, workflow_id):
    @activity.defn(name="generate_report")
    async def generate_report() -> str:
        return "reports/fake.json"

    async with Worker(env.client, task_queue=task_queue, workflows=[ReportWorkflow], activities=[generate_report]):
        result = await env.client.execute_workflow(
            ReportWorkflow.run, ReportParams(extra_seconds=3600), id=workflow_id, task_queue=task_queue
        )
    assert result == "reports/fake.json"


# ------------------------------------------------------------------ C. child workflows
@pytest.fixture
def inbox(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    folder = tmp_path / "inbox"
    folder.mkdir()
    for number in range(3):
        (folder / f"file{number}.txt").write_text("\n".join("a b c" for _ in range(5)), encoding="utf-8")
    return folder


async def run_parent(env, task_queue, workflow_id):
    async with Worker(
        env.client, task_queue=task_queue, workflows=[DataProcessingWorkflow], activities=[files.list_inbox_files]
    ), Worker(
        env.client,
        task_queue=FILES_TASK_QUEUE,
        workflows=[ProcessFileWorkflow],
        activities=[files.process_file, files.archive_file],
    ):
        return await env.client.execute_workflow(
            DataProcessingWorkflow.run, id=workflow_id, task_queue=task_queue
        )


async def test_parent_starts_one_child_per_file(env, task_queue, workflow_id, inbox, tmp_path):
    result = await run_parent(env, task_queue, workflow_id)

    assert result == {"files_found": 3, "files_processed": 3, "files_failed": 0, "lines": 15, "words": 45}
    assert list(inbox.glob("*.txt")) == []  # every file was moved...
    assert len(list((tmp_path / "processed").glob("*.txt"))) == 3  # ...to data/processed

    # the children are separate workflows with their own history
    child = env.client.get_workflow_handle(f"{workflow_id}-file-0")
    assert (await child.describe()).status.name == "COMPLETED"


async def test_one_broken_file_does_not_stop_the_others(env, task_queue, workflow_id, inbox):
    (inbox / "broken.txt").mkdir()  # matches *.txt but cannot be read as a file

    result = await run_parent(env, task_queue, workflow_id)

    assert result["files_found"] == 4
    assert result["files_processed"] == 3
    assert result["files_failed"] == 1


async def test_empty_inbox(env, task_queue, workflow_id, settings, tmp_path):
    settings.DATA_DIR = tmp_path  # no inbox folder at all
    result = await run_parent(env, task_queue, workflow_id)
    assert result["files_found"] == 0 and result["files_processed"] == 0
