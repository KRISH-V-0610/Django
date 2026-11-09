"""
Django settings for the POC 4 project (Temporal order processing).

Every secret / environment specific value comes from environment variables
(or the git-ignored .env file). See .env.example.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "testserver"])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "ninja",
    "orders",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

# Database: PostgreSQL by default. DB_ENGINE can be overridden (e.g. SQLite for a quick local check).
DATABASES = {
    "default": {
        "ENGINE": env("DB_ENGINE", default="django.db.backends.postgresql"),
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER", default=""),
        "PASSWORD": env("DB_PASSWORD", default=""),
        "HOST": env("DB_HOST", default=""),
        "PORT": env("DB_PORT", default=""),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

# Temporal
TEMPORAL_ADDRESS = env("TEMPORAL_ADDRESS", default="localhost:7233")
TEMPORAL_NAMESPACE = env("TEMPORAL_NAMESPACE", default="default")

# Orders above this total need a manual approval signal
ORDER_APPROVAL_THRESHOLD = env.int("ORDER_APPROVAL_THRESHOLD", default=1000)

# Emails are printed in the worker terminal instead of being sent
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "orders@example.com"

# Folders used by the file-processing and report demos (git-ignored)
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
