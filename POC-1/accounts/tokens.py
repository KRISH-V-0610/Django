import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone


def generate_token(user_id: int, token_type: str):
    now = timezone.now()

    lifetime = (
        settings.JWT_ACCESS_TOKEN_LIFETIME
        if token_type == "access"
        else settings.JWT_REFRESH_TOKEN_LIFETIME
    )

    jti = uuid.uuid4()
    expires_at = now + timedelta(seconds=lifetime)

    payload = {
        "user_id": user_id,
        "token_type": token_type,
        "jti": str(jti),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    token = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )

    return token, jti, expires_at


def generate_token_pair(user_id: int):
    access_token, _, _ = generate_token(user_id, "access")

    refresh_token, refresh_jti, refresh_expires_at = generate_token(
        user_id, "refresh"
    )

    tokens = {
        "access": access_token,
        "refresh": refresh_token,
        "token_type": "bearer",
    }

    return tokens, refresh_jti, refresh_expires_at