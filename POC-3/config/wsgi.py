"""WSGI entry point. Kept only to compare with ASGI; the project is meant to run on ASGI."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
