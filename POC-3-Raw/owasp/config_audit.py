"""
Checks the running settings against a short list of known misconfigurations (OWASP topic 4).
Each check returns (name, passed, advice). Used by `manage.py audit_config` and by the tests.
"""

WEAK_SECRETS = {"", "change-me", "change-me-too", "secret", "changeme"}


def audit_settings(settings):
    checks = []

    checks.append((
        "DEBUG is off",
        not settings.DEBUG,
        "DEBUG=True shows stack traces, settings and SQL to anyone. Set DEBUG=False.",
    ))
    checks.append((
        "SECRET_KEY is not a placeholder",
        settings.SECRET_KEY not in WEAK_SECRETS and len(settings.SECRET_KEY) >= 32,
        "Use a long random SECRET_KEY (50+ chars) from the environment.",
    ))
    checks.append((
        "JWT_SECRET_KEY is strong and separate from SECRET_KEY",
        settings.JWT_SECRET_KEY not in WEAK_SECRETS
        and len(settings.JWT_SECRET_KEY) >= 32
        and settings.JWT_SECRET_KEY != settings.SECRET_KEY,
        "A guessable JWT secret lets anyone forge tokens. Use a separate long random value.",
    ))
    checks.append((
        "ALLOWED_HOSTS is explicit",
        bool(settings.ALLOWED_HOSTS) and "*" not in settings.ALLOWED_HOSTS,
        "List the real host names. '*' allows Host-header attacks.",
    ))
    checks.append((
        "X-Content-Type-Options: nosniff",
        settings.SECURE_CONTENT_TYPE_NOSNIFF is True,
        "Set SECURE_CONTENT_TYPE_NOSNIFF = True.",
    ))
    checks.append((
        "Clickjacking protection (X-Frame-Options)",
        settings.X_FRAME_OPTIONS in ("DENY", "SAMEORIGIN")
        and "django.middleware.clickjacking.XFrameOptionsMiddleware" in settings.MIDDLEWARE,
        "Keep XFrameOptionsMiddleware and set X_FRAME_OPTIONS = 'DENY'.",
    ))
    return checks
