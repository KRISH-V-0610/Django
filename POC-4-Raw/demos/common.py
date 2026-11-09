"""Shared helpers for the demo scripts. Importing this module sets Django up."""
import uuid

from temporal_app.bootstrap import setup_django

setup_django()

from temporal_app.client import get_client  # noqa: E402,F401  (re-exported for the demos)


def short_id() -> str:
    """Workflow ids must be unique while the workflow runs. A random suffix keeps demos re-runnable."""
    return uuid.uuid4().hex[:8]


def title(text: str) -> None:
    print()
    print(f"=== {text} ===")


def explain_no_worker(command: str) -> None:
    print(f"(nothing happening? start a worker first: {command})")
