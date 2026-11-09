"""
Topic B: the simplest workflow.

    uv run python -m temporal_app.workers.run_worker --queue default     (terminal 1)
    uv run python -m demos.hello Krish                                   (terminal 2)
"""
import asyncio
import sys

from demos.common import get_client, short_id, title
from temporal_app.constants import DEFAULT_TASK_QUEUE
from temporal_app.workflows.demo import HelloWorkflow


async def main(name: str) -> None:
    client = await get_client()
    title("start HelloWorkflow")
    handle = await client.start_workflow(
        HelloWorkflow.run, name, id=f"hello-{short_id()}", task_queue=DEFAULT_TASK_QUEUE
    )
    print(f"started: workflow_id={handle.id} run_id={handle.result_run_id}")
    print("(open http://localhost:8233 to see its event history)")
    print("result:", await handle.result())


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "world"))
