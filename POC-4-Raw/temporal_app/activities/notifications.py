"""
Email notification and database update activities (topic D).
These are plain `def` (sync) activities: they use Django (mail, ORM), which is blocking code,
so the worker runs them in a thread pool (activity_executor).
"""
from django.conf import settings
from django.core.mail import send_mail
from temporalio import activity

from orders import services
from temporal_app.payloads import EmailParams


@activity.defn
def send_email(params: EmailParams) -> str:
    """Email notification. In this POC the console backend prints the mail in the worker terminal."""
    attempt = activity.info().attempt
    if attempt <= params.fail_attempts:
        raise ConnectionError(f"SMTP server unavailable (attempt {attempt})")
    send_mail(params.subject, params.body, settings.DEFAULT_FROM_EMAIL, [params.to])
    return f"email sent to {params.to}"


@activity.defn
def record_notification(params: EmailParams) -> int:
    """
    Database update activity. The workflow ID is the idempotency key, so a retry (or a
    replayed duplicate) updates the same row instead of inserting a second one.
    """
    log = services.record_notification(activity.info().workflow_id, params.to, params.subject)
    return log.pk
