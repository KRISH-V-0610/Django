"""
ASGI entry point. Run it with:   uv run uvicorn config.asgi:application --port 8000

One ASGI app that answers three kinds of scope:
    "lifespan"   -> our start-up / shut-down handler
    "websocket"  -> our WebSocket handler (core/websocket.py)
    "http"       -> Django (and so Django Ninja)
and a middleware around all of it (adds the timing header).
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.conf import settings  # noqa: E402  (loads settings and .env)

from core.telemetry import setup_telemetry  # noqa: E402

# Telemetry must start BEFORE Django builds its middleware stack, so the Django
# instrumentation can add its middleware to it.
setup_telemetry(
    "django-api",
    exporter=settings.OTEL_EXPORTER,
    endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
    django=True,
)

from django.core.asgi import get_asgi_application  # noqa: E402

django_app = get_asgi_application()

from core.asgi_lifespan import lifespan_app  # noqa: E402
from core.asgi_middleware import TimingMiddleware  # noqa: E402
from core.websocket import websocket_app  # noqa: E402


async def router(scope, receive, send):
    if scope["type"] == "lifespan":
        await lifespan_app(scope, receive, send)
    elif scope["type"] == "websocket":
        await websocket_app(scope, receive, send)
    else:
        await django_app(scope, receive, send)


application = TimingMiddleware(router)
