import uuid

import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def env():
    """
    A Temporal test server with TIME SKIPPING: while a test waits for a workflow result,
    the server jumps forward in time. A 24 hour approval timeout or a 30 second durable
    sleep finishes in milliseconds. The server binary is downloaded on the first run.
    """
    try:
        environment = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # no network on first run, or the download was blocked
        pytest.skip(f"could not start the Temporal test server: {exc}")
    yield environment
    await environment.shutdown()


@pytest.fixture
def task_queue() -> str:
    """A fresh queue name per test, so a leftover task of one test can never reach another."""
    return f"test-queue-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def workflow_id() -> str:
    return f"test-workflow-{uuid.uuid4().hex[:8]}"
