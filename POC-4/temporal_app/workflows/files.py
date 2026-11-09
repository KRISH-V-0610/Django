"""
File processing with a parent and child workflows (topic C).

DataProcessingWorkflow (parent) lists the files and starts ONE child workflow per file.
Children run in parallel, each with its own history, and can be watched separately in the UI.
This is the Temporal version of a Celery "group".
It is also the periodic data-processing job used by the schedule.
"""
import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities.files import archive_file, list_inbox_files, process_file
    from temporal_app.constants import DEFAULT_START_TO_CLOSE, FILES_TASK_QUEUE
    from temporal_app.payloads import FileParams, FileResult
    from temporal_app.policies import EXTERNAL_RETRY


@workflow.defn
class ProcessFileWorkflow:
    """Child workflow: process one file (with heartbeats), then move it to data/processed."""

    @workflow.run
    async def run(self, params: FileParams) -> FileResult:
        result = await workflow.execute_activity(
            process_file,
            params,
            start_to_close_timeout=timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=10),  # must heartbeat at least every 10s
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1), backoff_coefficient=2.0, maximum_attempts=3
            ),
        )
        await workflow.execute_activity(
            archive_file,
            params.path,
            start_to_close_timeout=DEFAULT_START_TO_CLOSE,
            retry_policy=EXTERNAL_RETRY,
        )
        return result


@workflow.defn
class DataProcessingWorkflow:
    """Parent workflow: one child per file in data/inbox, all in parallel."""

    @workflow.run
    async def run(self) -> dict:
        paths = await workflow.execute_activity(
            list_inbox_files, start_to_close_timeout=DEFAULT_START_TO_CLOSE
        )
        parent_id = workflow.info().workflow_id
        outcomes = await asyncio.gather(
            *[
                workflow.execute_child_workflow(
                    ProcessFileWorkflow.run,
                    FileParams(path=path),
                    id=f"{parent_id}-file-{index}",
                    task_queue=FILES_TASK_QUEUE,  # children run on the files workers
                )
                for index, path in enumerate(paths)
            ],
            return_exceptions=True,  # one broken file must not hide the others
        )
        results = [outcome for outcome in outcomes if isinstance(outcome, FileResult)]
        return {
            "files_found": len(paths),
            "files_processed": len(results),
            "files_failed": len(paths) - len(results),
            "lines": sum(r.lines for r in results),
            "words": sum(r.words for r in results),
        }
