"""
Data passed to and from workflows and activities.
Dataclasses are converted to JSON by Temporal, which is what gets stored in the event history.
"""
from dataclasses import dataclass, field


# ------------------------------------------------------------ demo activities
@dataclass
class EmailParams:
    to: str
    subject: str
    body: str
    fail_attempts: int = 0  # demo: fail the first N attempts


@dataclass
class ApiCallParams:
    name: str
    fail_attempts: int = 0  # demo: "the API is down" for the first N attempts


@dataclass
class SlowParams:
    sleep_seconds: float = 10.0
    bad_attempts: int = 1  # demo: only the first N attempts misbehave


@dataclass
class FileParams:
    path: str
    delay_per_line: float = 0.02
    crash_at_line: int = 0  # demo: on the first attempt, crash at this line (0 = never)


@dataclass
class FileResult:
    path: str
    lines: int
    words: int
    resumed_from_line: int


# -------------------------------------------------------------- demo workflows
@dataclass
class NotificationParams:
    recipient: str
    subject: str = "Hello from Temporal"
    api_fail_attempts: int = 0
    email_fail_attempts: int = 0


@dataclass
class ReportParams:
    extra_seconds: int = 0  # makes the report slow, used to show schedule overlap


@dataclass
class LongRunningParams:
    steps: int = 10
    pause_seconds: int = 5


# --------------------------------------------------------------- order workflow
@dataclass
class ValidationResult:
    requires_approval: bool
    customer_email: str
    total: str


@dataclass
class OrderResult:
    order_id: str
    status: str
    reason: str = ""
    email_sent: bool = False
    steps: list[str] = field(default_factory=list)
