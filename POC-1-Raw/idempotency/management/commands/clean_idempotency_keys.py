from django.core.management.base import BaseCommand
from django.utils import timezone

from idempotency.models import IdempotencyRecord


class Command(BaseCommand):
    help = "Delete expired idempotency keys (run it on a schedule, e.g. daily)."

    def handle(self, *args, **options):
        deleted, _ = IdempotencyRecord.objects.filter(expires_at__lt=timezone.now()).delete()
        self.stdout.write(f"Deleted {deleted} expired idempotency record(s).")
