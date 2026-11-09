"""Entry points (worker, demos, schedules CLI) call this first so Django settings and models work."""
import os


def setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
