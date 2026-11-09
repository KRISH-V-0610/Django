"""
Topic F: several workers on ONE task queue share the work.

    terminal 1:  uv run python -m temporal_app.workers.run_worker --queue default
    terminal 2:  uv run python -m temporal_app.workers.run_worker --queue default
    terminal 3:  uv run python -m demos.multi_worker --count 30

Every activity answers with its process id. With two workers you will see two different pids.
Kill one of them while it runs: the other one takes over its tasks.
"""
import argparse
import asyncio
import re
from collections import Counter

from demos.common import get_client, short_id, title
from temporal_app.constants import DEFAULT_TASK_QUEUE
from temporal_app.workflows.demo import HelloWorkflow


async def main(count: int) -> None:
    client = await get_client()
    title(f"starting {count} HelloWorkflows on {DEFAULT_TASK_QUEUE}")
    batch = short_id()
    results = await asyncio.gather(
        *[
            client.execute_workflow(
                HelloWorkflow.run, f"#{n}", id=f"multi-{batch}-{n}", task_queue=DEFAULT_TASK_QUEUE
            )
            for n in range(count)
        ]
    )
    pids = Counter(re.search(r"process (\d+)", text).group(1) for text in results)
    print("activities handled per worker process:", dict(pids))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    asyncio.run(main(parser.parse_args().count))
