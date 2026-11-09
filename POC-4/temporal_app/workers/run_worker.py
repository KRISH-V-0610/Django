"""
Start a Temporal worker.

    uv run python -m temporal_app.workers.run_worker                    # all queues in one process
    uv run python -m temporal_app.workers.run_worker --queue orders     # only the orders queue
    uv run python -m temporal_app.workers.run_worker --queue email      # dedicated email worker
    uv run python -m temporal_app.workers.run_worker --queue default --max-activities 5

A worker is an ordinary long-running process. It POLLS the server for tasks on its queue,
runs the workflow or activity code, and reports the result back. It stores nothing:
all state is in the Temporal server's event history, so a worker can be stopped,
crashed, restarted or duplicated at any time.
"""
import argparse
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from temporal_app.bootstrap import setup_django

setup_django()  # must happen before the activities (and Django models) are imported

from temporalio.worker import Worker  # noqa: E402

from temporal_app.client import get_client  # noqa: E402
from temporal_app.constants import QUEUE_ALIASES  # noqa: E402
from temporal_app.workers.registry import get_queue_configs  # noqa: E402

logger = logging.getLogger("worker")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a Temporal worker")
    parser.add_argument(
        "--queue",
        nargs="+",
        default=["all"],
        choices=[*QUEUE_ALIASES, "all"],
        help="queue(s) to listen on (default: all)",
    )
    parser.add_argument(
        "--max-activities", type=int, default=10, help="activities running at the same time"
    )
    parser.add_argument(
        "--max-workflow-tasks", type=int, default=10, help="workflow tasks running at the same time"
    )
    return parser.parse_args(argv)


def build_workers(client, queue_names, max_activities, max_workflow_tasks, executor):
    configs = get_queue_configs()
    workers = []
    for name in queue_names:
        queue = QUEUE_ALIASES[name]
        config = configs[queue]
        workers.append(
            Worker(
                client,
                task_queue=queue,
                workflows=config.workflows,
                activities=config.activities,
                activity_executor=executor,  # needed for the sync (def) activities
                max_concurrent_activities=max_activities,
                max_concurrent_workflow_tasks=max_workflow_tasks,
                identity=f"{queue}@pid{os.getpid()}",
            )
        )
    return workers


async def main(argv=None) -> None:
    args = parse_args(argv)
    names = list(QUEUE_ALIASES) if "all" in args.queue else args.queue
    client = await get_client()
    executor = ThreadPoolExecutor(max_workers=args.max_activities * len(names))
    workers = build_workers(client, names, args.max_activities, args.max_workflow_tasks, executor)
    logger.info("worker pid %s listening on: %s", os.getpid(), ", ".join(QUEUE_ALIASES[n] for n in names))
    await asyncio.gather(*(worker.run() for worker in workers))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("worker stopped")
