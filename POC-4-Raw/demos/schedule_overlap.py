"""
Topic I: schedule overlap policy, SKIP versus BUFFER_ONE.

Two schedules tick every 5 seconds, but every run of the report takes about 12 seconds,
so a tick always arrives while the previous run is still going.

    terminal 1:  uv run python -m temporal_app.workers.run_worker --queue default
    terminal 2:  uv run python -m demos.schedule_overlap            (runs about 50 seconds)

  SKIP        : the tick is dropped. Counted in "skipped (overlap)".
  BUFFER_ONE  : ONE tick is remembered and starts the moment the running one finishes, the
                others are dropped. So it runs back to back and does more runs than SKIP.

The counters are read from the server at the end and then both schedules are deleted.
"""
import argparse
import asyncio

from demos.common import get_client, title
from temporal_app.schedules import build_schedule

IDS = {"skip": "overlap-demo-skip", "buffer_one": "overlap-demo-buffer-one"}


async def main(seconds: int) -> None:
    client = await get_client()
    for overlap, schedule_id in IDS.items():
        schedule = build_schedule(
            "report", schedule_id, every_seconds=5, overlap=overlap, report_seconds=12
        )
        await client.create_schedule(schedule_id, schedule)
        print(f"created {schedule_id} (overlap={overlap})")

    title(f"waiting {seconds}s while the schedules tick")
    try:
        await asyncio.sleep(seconds)
        title("result")
        for overlap, schedule_id in IDS.items():
            info = (await client.get_schedule_handle(schedule_id).describe()).info
            print(
                f"{overlap:11} runs started: {info.num_actions:2}   "
                f"skipped (overlap): {info.num_actions_skipped_overlap:2}   "
                f"running now: {len(info.running_actions)}"
            )
    finally:
        for schedule_id in IDS.values():
            await client.get_schedule_handle(schedule_id).delete()
        print("deleted both schedules")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=50)
    asyncio.run(main(parser.parse_args().seconds))
