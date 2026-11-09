"""
ASGI config. Run the API with:  uv run uvicorn config.asgi:application --port 8000

The API talks to Temporal with an async client that lives on uvicorn's event loop,
so it should be served with ASGI (not runserver / WSGI).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
