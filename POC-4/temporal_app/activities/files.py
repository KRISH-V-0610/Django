"""
File processing activities (topic D): a long-running activity with HEARTBEATS.

The heartbeat does two jobs:
  1. tells the server "I am alive" (so a hung worker is noticed quickly), and
  2. saves progress. If the attempt dies, the retry reads it back from
     activity.info().heartbeat_details and continues instead of starting over.
"""
import asyncio
import shutil
from pathlib import Path

from django.conf import settings
from temporalio import activity

from temporal_app.payloads import FileParams, FileResult

HEARTBEAT_EVERY_LINES = 10


def inbox_dir() -> Path:
    return Path(settings.DATA_DIR) / "inbox"


def processed_dir() -> Path:
    return Path(settings.DATA_DIR) / "processed"


@activity.defn
async def list_inbox_files() -> list[str]:
    """Names of the .txt files waiting in data/inbox."""
    folder = inbox_dir()
    if not folder.exists():
        return []
    return sorted(str(path) for path in folder.glob("*.txt"))


@activity.defn
async def process_file(params: FileParams) -> FileResult:
    """Count lines and words of a file, one line at a time, heartbeating as we go."""
    info = activity.info()
    start_line, words = 0, 0
    if info.heartbeat_details:  # a previous attempt got this far
        start_line, words = info.heartbeat_details[0], info.heartbeat_details[1]
        activity.logger.info("resuming %s from line %s", params.path, start_line)

    lines = Path(params.path).read_text(encoding="utf-8").splitlines()
    for number in range(start_line, len(lines)):
        if params.crash_at_line and info.attempt == 1 and number == params.crash_at_line:
            raise RuntimeError(f"simulated crash at line {number}")
        words += len(lines[number].split())
        await asyncio.sleep(params.delay_per_line)  # also where a cancellation request lands
        if (number + 1) % HEARTBEAT_EVERY_LINES == 0:
            activity.heartbeat(number + 1, words)

    return FileResult(path=params.path, lines=len(lines), words=words, resumed_from_line=start_line)


@activity.defn
async def archive_file(path: str) -> str:
    """Move a processed file to data/processed. Safe to repeat: already moved means done."""
    source = Path(path)
    target_folder = processed_dir()
    target_folder.mkdir(parents=True, exist_ok=True)
    target = target_folder / source.name
    if source.exists():
        shutil.move(str(source), str(target))
    return str(target)
