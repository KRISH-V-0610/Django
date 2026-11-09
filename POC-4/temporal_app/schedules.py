"""
Temporal Schedules (topic I): the replacement for Celery Beat.

    uv run python -m temporal_app.schedules create report --every 60
    uv run python -m temporal_app.schedules create report --daily-at 09:30 --weekdays-only
    uv run python -m temporal_app.schedules create notification --every 120 --recipient me@example.com
    uv run python -m temporal_app.schedules create processing --every 300
    uv run python -m temporal_app.schedules create report --id report-skip --every 5 --report-seconds 12 --overlap skip
    uv run python -m temporal_app.schedules list
    uv run python -m temporal_app.schedules describe report-schedule
    uv run python -m temporal_app.schedules pause report-schedule
    uv run python -m temporal_app.schedules resume report-schedule
    uv run python -m temporal_app.schedules trigger report-schedule
    uv run python -m temporal_app.schedules backfill report-schedule --minutes 10
    uv run python -m temporal_app.schedules delete report-schedule

Unlike Celery Beat, the schedule lives INSIDE the Temporal server (not in a beat process
that can die or run twice). Each tick starts a normal workflow, so retries, history and
the UI all work for scheduled runs too.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from temporal_app.bootstrap import setup_django

setup_django()  # the workflow modules import Django models through the activities

from temporalio.client import (  # noqa: E402
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleBackfill,
    ScheduleCalendarSpec,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.service import RPCError  # noqa: E402

from temporal_app.client import get_client  # noqa: E402
from temporal_app.constants import (  # noqa: E402
    DEFAULT_TASK_QUEUE,
    NOTIFICATION_SCHEDULE_ID,
    PROCESSING_SCHEDULE_ID,
    REPORT_SCHEDULE_ID,
)
from temporal_app.payloads import NotificationParams, ReportParams  # noqa: E402
from temporal_app.workflows.demo import NotificationWorkflow, ReportWorkflow  # noqa: E402
from temporal_app.workflows.files import DataProcessingWorkflow  # noqa: E402

KINDS = ("report", "notification", "processing")
DEFAULT_IDS = {
    "report": REPORT_SCHEDULE_ID,
    "notification": NOTIFICATION_SCHEDULE_ID,
    "processing": PROCESSING_SCHEDULE_ID,
}
OVERLAP_CHOICES = [policy.name.lower() for policy in ScheduleOverlapPolicy]


# --------------------------------------------------------------------- builders
def parse_daily_at(value: str) -> tuple[int, int]:
    """'09:30' -> (9, 30). Raises ValueError for anything else."""
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        raise ValueError("daily-at must look like HH:MM, for example 09:30") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("daily-at: hour must be 0-23 and minute 0-59")
    return hour, minute


def build_spec(
    *, every_seconds: int | None = None, daily_at: str | None = None, weekdays_only: bool = False
) -> ScheduleSpec:
    """
    WHEN to run. Two styles:
      interval  -> "every N seconds"            (like Celery's  schedule=30.0)
      calendar  -> "at 09:30, optionally only Mon-Fri"   (like Celery's crontab(hour=9, minute=30))
    """
    if daily_at:
        hour, minute = parse_daily_at(daily_at)
        days = (ScheduleRange(1, 5),) if weekdays_only else (ScheduleRange(0, 6),)  # 0 = Sunday
        return ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(
                    hour=(ScheduleRange(hour),), minute=(ScheduleRange(minute),), day_of_week=days
                )
            ]
        )
    seconds = every_seconds or 60
    if seconds < 1:
        raise ValueError("every must be at least 1 second")
    return ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=seconds))])


def build_policy(*, overlap: str = "skip", catchup_minutes: int = 10) -> SchedulePolicy:
    """
    overlap:        what to do if the previous run is still going when the next tick arrives
                      skip       = ignore this tick
                      buffer_one = remember ONE tick and start it as soon as the running one ends
    catchup_window: if the Temporal server was down, run the ticks missed in the last N minutes
                    once it is back. Older missed ticks are dropped.
    """
    return SchedulePolicy(
        overlap=ScheduleOverlapPolicy[overlap.upper()],
        catchup_window=timedelta(minutes=catchup_minutes),
    )


def build_action(kind: str, schedule_id: str, *, report_seconds: int = 0, recipient: str = "team@example.com"):
    """WHAT to run on every tick: start one workflow."""
    run_id_prefix = f"{schedule_id}-run"  # Temporal adds the tick time, so each run id is unique
    if kind == "report":
        return ScheduleActionStartWorkflow(
            ReportWorkflow.run,
            ReportParams(extra_seconds=report_seconds),
            id=run_id_prefix,
            task_queue=DEFAULT_TASK_QUEUE,
        )
    if kind == "notification":
        return ScheduleActionStartWorkflow(
            NotificationWorkflow.run,
            NotificationParams(recipient=recipient, subject="Scheduled notification"),
            id=run_id_prefix,
            task_queue=DEFAULT_TASK_QUEUE,
        )
    if kind == "processing":
        return ScheduleActionStartWorkflow(
            DataProcessingWorkflow.run, id=run_id_prefix, task_queue=DEFAULT_TASK_QUEUE
        )
    raise ValueError(f"unknown schedule kind {kind!r}, use one of {KINDS}")


def build_schedule(
    kind: str,
    schedule_id: str,
    *,
    every_seconds: int | None = None,
    daily_at: str | None = None,
    weekdays_only: bool = False,
    overlap: str = "skip",
    catchup_minutes: int = 10,
    report_seconds: int = 0,
    recipient: str = "team@example.com",
    paused: bool = False,
) -> Schedule:
    return Schedule(
        action=build_action(kind, schedule_id, report_seconds=report_seconds, recipient=recipient),
        spec=build_spec(every_seconds=every_seconds, daily_at=daily_at, weekdays_only=weekdays_only),
        policy=build_policy(overlap=overlap, catchup_minutes=catchup_minutes),
        state=ScheduleState(note=f"{kind} schedule created by the POC CLI", paused=paused),
    )


# --------------------------------------------------------------------- commands
async def cmd_create(client: Client, args) -> None:
    schedule_id = args.id or DEFAULT_IDS[args.kind]
    schedule = build_schedule(
        args.kind,
        schedule_id,
        every_seconds=args.every,
        daily_at=args.daily_at,
        weekdays_only=args.weekdays_only,
        overlap=args.overlap,
        catchup_minutes=args.catchup_minutes,
        report_seconds=args.report_seconds,
        recipient=args.recipient,
        paused=args.paused,
    )
    try:
        await client.create_schedule(schedule_id, schedule)
    except ScheduleAlreadyRunningError:
        print(f"schedule {schedule_id!r} already exists. Delete it first: ... delete {schedule_id}")
        return
    print(f"created schedule {schedule_id!r} ({args.kind}, overlap={args.overlap})")


async def cmd_list(client: Client, args) -> None:
    found = False
    async for item in await client.list_schedules():
        found = True
        paused = item.schedule.state.paused if item.schedule else "?"
        next_times = item.info.next_action_times[:1] if item.info else []
        print(f"{item.id:30} paused={paused}  next={[t.isoformat() for t in next_times]}")
    if not found:
        print("no schedules")


async def cmd_describe(client: Client, args) -> None:
    desc = await client.get_schedule_handle(args.id).describe()
    info = desc.info
    print(f"schedule        : {desc.id}")
    print(f"paused          : {desc.schedule.state.paused}  ({desc.schedule.state.note})")
    print(f"overlap policy  : {desc.schedule.policy.overlap.name}")
    print(f"catch-up window : {desc.schedule.policy.catchup_window}")
    print(f"actions started : {info.num_actions}")
    print(f"skipped (overlap): {info.num_actions_skipped_overlap}")
    print(f"missed catch-up : {info.num_actions_missed_catchup_window}")
    print(f"running now     : {[a.workflow_id for a in info.running_actions]}")
    print(f"next runs       : {[t.isoformat() for t in info.next_action_times[:3]]}")
    for recent in info.recent_actions[-3:]:
        print(f"recent run      : {recent.action.workflow_id} at {recent.started_at.isoformat()}")


async def cmd_pause(client: Client, args) -> None:
    await client.get_schedule_handle(args.id).pause(note="paused from the CLI")
    print(f"paused {args.id!r} (no new runs; running ones are not touched)")


async def cmd_resume(client: Client, args) -> None:
    await client.get_schedule_handle(args.id).unpause(note="resumed from the CLI")
    print(f"resumed {args.id!r}")


async def cmd_trigger(client: Client, args) -> None:
    await client.get_schedule_handle(args.id).trigger()
    print(f"triggered one run of {args.id!r} right now")


async def cmd_backfill(client: Client, args) -> None:
    now = datetime.now(timezone.utc)
    window = ScheduleBackfill(
        start_at=now - timedelta(minutes=args.minutes),
        end_at=now,
        overlap=ScheduleOverlapPolicy.ALLOW_ALL,
    )
    await client.get_schedule_handle(args.id).backfill(window)
    print(f"backfilled the last {args.minutes} minute(s) of {args.id!r}")


async def cmd_delete(client: Client, args) -> None:
    await client.get_schedule_handle(args.id).delete()
    print(f"deleted {args.id!r}")


# --------------------------------------------------------------------------- CLI
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Manage Temporal schedules")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="create a schedule")
    create.set_defaults(func=cmd_create)
    create.add_argument("kind", choices=KINDS)
    create.add_argument("--id", help="schedule id (default depends on the kind)")
    when = create.add_mutually_exclusive_group()
    when.add_argument("--every", type=int, help="interval spec: every N seconds (default 60)")
    when.add_argument("--daily-at", help="calendar spec: HH:MM every day")
    create.add_argument("--weekdays-only", action="store_true", help="with --daily-at: Monday to Friday only")
    create.add_argument("--overlap", choices=OVERLAP_CHOICES, default="skip")
    create.add_argument("--catchup-minutes", type=int, default=10)
    create.add_argument("--report-seconds", type=int, default=0, help="report only: make each run slow")
    create.add_argument("--recipient", default="team@example.com", help="notification only")
    create.add_argument("--paused", action="store_true", help="create it paused")

    sub.add_parser("list", help="list all schedules").set_defaults(func=cmd_list)

    for name, func, helptext in [
        ("describe", cmd_describe, "show counters, next runs, recent runs"),
        ("pause", cmd_pause, "stop starting new runs"),
        ("resume", cmd_resume, "start scheduling again"),
        ("trigger", cmd_trigger, "run once right now"),
        ("delete", cmd_delete, "delete the schedule"),
    ]:
        command = sub.add_parser(name, help=helptext)
        command.set_defaults(func=func)
        command.add_argument("id")

    backfill = sub.add_parser("backfill", help="run the ticks of the last N minutes now")
    backfill.set_defaults(func=cmd_backfill)
    backfill.add_argument("id")
    backfill.add_argument("--minutes", type=int, default=10)
    return parser.parse_args(argv)


async def main(argv=None) -> None:
    args = parse_args(argv)
    client = await get_client()
    try:
        await args.func(client, args)
    except RPCError as exc:
        print(f"Temporal said: {exc.status.name}: {exc.message}")


if __name__ == "__main__":
    asyncio.run(main())
