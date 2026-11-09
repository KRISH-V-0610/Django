"""Settings used by pytest. Same as settings.py, with safe test values and no network."""
import os

# set BEFORE settings.py is imported, because it reads these from the environment
os.environ.setdefault("SECRET_KEY", "test-secret-key-only-for-pytest-runs-0123456789")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-only-for-pytest-runs-9876543210")
os.environ.setdefault("DB_NAME", "django_poc3")
os.environ["OTEL_EXPORTER"] = "none"  # tests must never try to reach Jaeger

from .settings import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast, tests only
EXTERNAL_SERVICE_LATENCY = 0
