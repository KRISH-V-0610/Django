"""
Security logging (OWASP topic 7).

Every security-relevant event goes through log_event() so the lines look the same:
    security_event event='login_failed' ip='127.0.0.1' path='/api/auth/login' user='alice' trace_id='...'
Secrets are masked, and the trace id ties the log line to the trace in Jaeger.
"""
import logging

from opentelemetry import trace

logger = logging.getLogger("security")

SENSITIVE_KEYS = {"password", "token", "access", "authorization", "secret", "api_key", "cookie"}


def mask_sensitive(data):
    """Return a copy of the dict where secret-looking values are replaced by '***'."""
    return {
        key: "***" if key.lower() in SENSITIVE_KEYS else value
        for key, value in data.items()
    }


def _trace_id():
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else "-"


def log_event(event, request=None, **fields):
    data = {"event": event}
    if request is not None:
        # REMOTE_ADDR on purpose: X-Forwarded-For can be forged by the client.
        data["ip"] = request.META.get("REMOTE_ADDR", "-")
        data["path"] = request.path
        user = getattr(request, "auth", None)
        if user is not None and hasattr(user, "username"):
            data["user"] = user.username
    data.update(mask_sensitive(fields))
    data["trace_id"] = _trace_id()
    line = " ".join(f"{key}={value!r}" for key, value in data.items())
    logger.warning("security_event %s", line)
