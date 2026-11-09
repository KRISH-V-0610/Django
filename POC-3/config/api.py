"""One NinjaAPI that mounts the router of every app."""
import logging
import uuid

from ninja import NinjaAPI
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from accounts.api import router as auth_router
from core.api import router as demo_router
from documents.api import secure_router as documents_router
from documents.api import vulnerable_router as vulnerable_documents_router
from jobs.api import router as jobs_router
from owasp.api import router as owasp_router

logger = logging.getLogger(__name__)

api = NinjaAPI(title="POC-3 API", version="1.0.0")

api.add_router("/auth", auth_router)
api.add_router("/demo", demo_router)
api.add_router("/jobs", jobs_router)
api.add_router("/documents", documents_router)
api.add_router("/vulnerable/documents", vulnerable_documents_router)
api.add_router("/owasp", owasp_router)


@api.get("/health", tags=["health"])
def health(request):
    return {"status": "ok"}


@api.exception_handler(Exception)
def unhandled_exception(request, exc):
    """
    Secure error handling (OWASP topic 6): the client only gets a generic message and an
    error id. The full stack trace goes to the server log and to the active trace span,
    so a developer can find it with that id, but an attacker learns nothing.
    """
    error_id = uuid.uuid4().hex[:12]
    logger.error("unhandled error id=%s path=%s", error_id, request.path, exc_info=exc)

    span = trace.get_current_span()
    span.set_attribute("error.id", error_id)
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))

    return api.create_response(
        request,
        {"detail": "Internal server error", "error_id": error_id},
        status=500,
    )
