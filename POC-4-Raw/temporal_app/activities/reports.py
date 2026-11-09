"""Report generation activity, used by the recurring (scheduled) report workflow."""
import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from temporalio import activity

from orders import services


@activity.defn
def generate_report() -> str:
    """Collect order numbers from the database and write them to reports/report-<time>.json."""
    folder = Path(settings.REPORTS_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = folder / f"report-{now:%Y%m%d-%H%M%S}.json"
    path.write_text(
        json.dumps({"generated_at": now.isoformat(), "orders": services.build_report()}, indent=2),
        encoding="utf-8",
    )
    activity.logger.info("report written to %s", path)
    return str(path)
