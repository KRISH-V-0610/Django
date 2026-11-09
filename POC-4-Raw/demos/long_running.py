"""
Topics C and E: a long-running workflow and the worker restart / recovery demo.

    terminal 1:  uv run python -m temporal_app.workers.run_worker --queue default
    terminal 2:  uv run python -m demos.long_running
    then in terminal 1: press Ctrl+C (kills the worker mid-workflow), wait ~10 seconds,
    start it again. The progress printed in terminal 2 pauses while no worker is alive and
    then continues from the same step. Nothing is lost: the state is in Temporal's history.
"""
import argparse
import asyncio
from datetime import timedelta

from temporalio.client import WorkflowQueryFailedError
from temporalio.service import RPCError

from demos.common import get_client, short_id, title
from temporal_app.constants import DEFAULT_TASK_QUEUE
from temporal_app.payloads import LongRunningParams
from temporal_app.workflows.demo import LongRunningWorkflow


async def main(args) -> None:
    client = await get_client()
    title("start LongRunningWorkflow")
    handle = await client.start_workflow(
        LongRunningWorkflow.run,
        LongRunningParams(steps=args.steps, pause_seconds=args.pause),
        id=f"long-running-{short_id()}",
        task_queue=DEFAULT_TASK_QUEUE,
    )
    print(f"started: {handle.id}")
    result_task = asyncio.ensure_future(handle.result())
    while not result_task.done():
        try:
            progress = await handle.query(LongRunningWorkflow.progress, rpc_timeout=timedelta(seconds=3))
            print("progress:", progress)
        except (RPCError, WorkflowQueryFailedError):
            print("no worker answered the query (stopped? start it again, the workflow keeps its place)")
        await asyncio.wait({result_task}, timeout=2)
    print("result:", await result_task)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--pause", type=int, default=5, help="seconds between steps (durable timer)")
    asyncio.run(main(parser.parse_args()))
