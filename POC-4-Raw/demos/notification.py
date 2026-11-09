"""
Topics C, D, F: multi-step, multi-activity workflow on TWO task queues.

The email activity is routed to the email queue. Try this order:

    terminal 1:  uv run python -m temporal_app.workers.run_worker --queue default
    terminal 2:  uv run python -m demos.notification --api-fails 2
                 -> the API step is retried, then the workflow WAITS: nobody listens on email-queue
    terminal 3:  uv run python -m temporal_app.workers.run_worker --queue email
                 -> the waiting email activity is picked up and the workflow finishes
"""
import argparse
import asyncio

from demos.common import get_client, short_id, title
from temporal_app.constants import DEFAULT_TASK_QUEUE
from temporal_app.payloads import NotificationParams
from temporal_app.workflows.demo import NotificationWorkflow


async def main(args) -> None:
    client = await get_client()
    params = NotificationParams(
        recipient=args.to, api_fail_attempts=args.api_fails, email_fail_attempts=args.email_fails
    )
    title("start NotificationWorkflow")
    handle = await client.start_workflow(
        NotificationWorkflow.run, params, id=f"notification-{short_id()}", task_queue=DEFAULT_TASK_QUEUE
    )
    print(f"started: {handle.id}")
    try:
        result = await asyncio.wait_for(asyncio.shield(handle.result()), timeout=args.hint_after)
    except asyncio.TimeoutError:
        print(f"still running after {args.hint_after}s. Is the email worker running?")
        print("   uv run python -m temporal_app.workers.run_worker --queue email")
        result = await handle.result()
    print("result:", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", default="customer@example.com")
    parser.add_argument("--api-fails", type=int, default=0, help="API fails this many times first")
    parser.add_argument("--email-fails", type=int, default=0, help="email fails this many times first (max 2 recover)")
    parser.add_argument("--hint-after", type=float, default=8.0)
    asyncio.run(main(parser.parse_args()))
