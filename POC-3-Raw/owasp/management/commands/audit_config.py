"""uv run python manage.py audit_config   -> PASS/FAIL per check, exit code 1 if anything fails."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from owasp.config_audit import audit_settings


class Command(BaseCommand):
    help = "Check the current settings for common security misconfigurations."

    def handle(self, *args, **options):
        failed = 0
        for name, passed, advice in audit_settings(settings):
            if passed:
                self.stdout.write(self.style.SUCCESS(f"PASS  {name}"))
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"FAIL  {name}"))
                self.stdout.write(f"      -> {advice}")
        if failed:
            raise CommandError(f"{failed} check(s) failed")
        self.stdout.write(self.style.SUCCESS("All checks passed."))
