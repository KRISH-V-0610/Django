"""
Create jobs for a failure scenario and watch what the workers do with them.

    uv run python manage.py jobs_demo success
    uv run python manage.py jobs_demo retry        # fails twice, succeeds on the 3rd delivery
    uv run python manage.py jobs_demo dlq          # always fails -> dead-letter stream
    uv run python manage.py jobs_demo crash        # kills the worker that picks it up
    uv run python manage.py jobs_demo duplicate    # the same job is published twice
    uv run python manage.py jobs_demo many --count 10   # run with 2+ workers

Workers must already be running (python manage.py run_worker --name worker-1).
"""
import time

from django.core.management.base import BaseCommand, CommandError

from jobs.models import Job
from jobs.producer import enqueue_job

SCENARIOS = {
    "success": [("email", {"to": "a@example.com"}), ("file", {"lines": 5000}), ("report", {})],
    "retry": [("email", {"to": "retry@example.com", "fail_times": 2})],
    "dlq": [("email", {"to": "broken@example.com", "always_fail": True})],
    "crash": [("report", {"name": "crash-report", "crash_on_attempt": 1})],
    "duplicate": [("email", {"to": "twice@example.com"})],
}


class Command(BaseCommand):
    help = "Publish demo jobs and print how they end."

    def add_arguments(self, parser):
        parser.add_argument("scenario", choices=[*SCENARIOS, "many"])
        parser.add_argument("--count", type=int, default=10, help="jobs for 'many'")
        parser.add_argument("--timeout", type=int, default=60, help="seconds to wait for results")

    def handle(self, *args, **options):
        scenario = options["scenario"]
        if scenario == "many":
            specs = [("email", {"to": f"user{i}@example.com"}) for i in range(options["count"])]
        else:
            specs = SCENARIOS[scenario]

        jobs = [Job.objects.create(kind=kind, payload=payload) for kind, payload in specs]
        for job in jobs:
            enqueue_job(job)
        if scenario == "duplicate":
            enqueue_job(jobs[0])  # a second message for the SAME job
            self.stdout.write("published the same job twice")
        self.stdout.write(f"published {len(jobs)} job(s); waiting up to {options['timeout']}s ...")

        self.wait_and_report(jobs, options["timeout"])

    def wait_and_report(self, jobs, timeout):
        ids = [job.pk for job in jobs]
        finished = (Job.Status.DONE, Job.Status.DEAD)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not Job.objects.filter(pk__in=ids).exclude(status__in=finished).exists():
                break
            time.sleep(1)

        self.stdout.write("")
        self.stdout.write(f"{'job':36}  {'status':10}  tries  {'worker':10}  effect")
        for job in Job.objects.filter(pk__in=ids).select_related("effect").order_by("created_at"):
            effect = getattr(job, "effect", None)
            self.stdout.write(
                f"{job.id}  {job.status:10}  {job.attempts:<5}  {job.processed_by:10}  "
                f"{effect.description if effect else job.last_error}"
            )
        unfinished = Job.objects.filter(pk__in=ids).exclude(status__in=finished).count()
        if unfinished:
            raise CommandError(f"{unfinished} job(s) not finished: is a worker running?")
