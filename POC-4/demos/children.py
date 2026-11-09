"""
Topics C and D: child workflows and a file-processing activity with heartbeats.

    uv run python -m temporal_app.workers.run_worker --queue default files   (terminal 1)
    uv run python -m demos.children                  # parent + one child per file
    uv run python -m demos.children --resume-demo    # one file, crashes once, resumes from a heartbeat

The sample files are created in data/inbox (git-ignored).
"""
import argparse
import asyncio
from pathlib import Path

from django.conf import settings

from demos.common import get_client, short_id, title
from temporal_app.constants import DEFAULT_TASK_QUEUE, FILES_TASK_QUEUE
from temporal_app.payloads import FileParams
from temporal_app.workflows.files import DataProcessingWorkflow, ProcessFileWorkflow


def create_sample_files(count: int = 3, lines: int = 60) -> list[Path]:
    inbox = Path(settings.DATA_DIR) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    paths = []
    for number in range(1, count + 1):
        path = inbox / f"sample-{short_id()}-{number}.txt"
        path.write_text("\n".join(f"line {i} of file {number} with some words" for i in range(lines)), encoding="utf-8")
        paths.append(path)
    return paths


async def main(resume_demo: bool) -> None:
    client = await get_client()
    if resume_demo:
        path = create_sample_files(count=1, lines=60)[0]
        title("ProcessFileWorkflow: attempt 1 crashes at line 25")
        result = await client.execute_workflow(
            ProcessFileWorkflow.run,
            FileParams(path=str(path), delay_per_line=0.3, crash_at_line=25),
            id=f"file-resume-{short_id()}",
            task_queue=FILES_TASK_QUEUE,
        )
        print(result)
        print(f"resumed_from_line={result.resumed_from_line}: attempt 2 continued from the last heartbeat, not from 0")
        return

    paths = create_sample_files()
    title(f"DataProcessingWorkflow: {len(paths)} files -> {len(paths)} child workflows")
    result = await client.execute_workflow(
        DataProcessingWorkflow.run, id=f"processing-{short_id()}", task_queue=DEFAULT_TASK_QUEUE
    )
    print(result)
    print("children are visible in the UI as separate workflows: <parent-id>-file-0, -file-1, ...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-demo", action="store_true")
    asyncio.run(main(parser.parse_args().resume_demo))
