from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone


def create_access_token(user_id):
    now = timezone.now()
    payload = {
        "user_id": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.JWT_ACCESS_TOKEN_LIFETIME)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")
