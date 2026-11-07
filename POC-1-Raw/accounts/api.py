import uuid

import jwt

from ninja import Router
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import RefreshToken

from .schemas import RegisterSchema, UserOut, LoginSchema, TokenResponse, ErrorSchema, LogoutSchema, RefreshSchema
from django.conf import settings
from .tokens import generate_token_pair
from .auth import jwt_auth


router = Router()


@router.post("/register", response={201: UserOut, 400: ErrorSchema})
def register(request, data: RegisterSchema):
    User = get_user_model()

    # Validate password against Django's configured validators
    try:
        validate_password(data.password)
    except ValidationError as exc:
        return 400, {"detail": " ".join(exc.messages)}

    # Create user safely
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=data.username,
                email=data.email,
                password=data.password
            )
    except IntegrityError:
        return 400, {"detail": "Username already exists."}

    return 201, {
        "id": user.id,
        "username": user.username,
        "email": user.email
    }

@router.post(
    "/login",
    response={
        200: TokenResponse,
        401: ErrorSchema,
    },
)
def login(request, data: LoginSchema):

    user = authenticate(
        request,
        username=data.username,
        password=data.password,
    )

    if user is None or not user.is_active: 
        return 401, {"detail": "Invalid username or password"}

    # tokens = generate_token_pair(user.id)
    with transaction.atomic():
        tokens, refresh_jti, refresh_expires_at = (
            generate_token_pair(user.id)
        )

        RefreshToken.objects.create(
            user=user,
            jti=refresh_jti,
            expires_at=refresh_expires_at,
        )
    

    return 200, tokens


@router.post(
    "/logout",
    response={
        200: dict,
        401: ErrorSchema,
    },
)
def logout(request, data: LogoutSchema):

    try:
        payload = jwt.decode(
            data.refresh,
            settings.JWT_SECRET_KEY,
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError:
        return 401, {"detail": "Invalid or expired refresh token"}
  
    if payload.get("token_type") != "refresh":
        return 401, {"detail": "Invalid refresh token"}
    
    try:
        jti = uuid.UUID(payload["jti"])
    except (KeyError, ValueError, TypeError):
        return 401, {"detail": "Invalid refresh token"}

    now = timezone.now()

    revoked_count = RefreshToken.objects.filter(
        jti=jti,
        user_id=payload.get("user_id"),
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).update(revoked_at=now)

    if revoked_count == 0:
        return 401, {"detail": "Refresh token already revoked or invalid"}

    return 200, {"detail": "Logged out successfully"}


@router.post(
    "/refresh",
    response={
        200: TokenResponse,
        401: ErrorSchema,
    },
)
def refresh_tokens(request, data: RefreshSchema):

    # 1. Verify the refresh JWT
    try:
        payload = jwt.decode(
            data.refresh,
            settings.JWT_SECRET_KEY,
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError:
        return 401, {"detail": "Invalid or expired refresh token"}

    # 2. Ensure this is a refresh token, not an access token
    if payload.get("token_type") != "refresh":
        return 401, {"detail": "Invalid refresh token"}

    # 3. Extract the unique token ID
    try:
        jti = uuid.UUID(payload["jti"])
    except (KeyError, ValueError, TypeError):
        return 401, {"detail": "Invalid refresh token"}

    # 4. Lock and validate the refresh-token record
    with transaction.atomic():
        now = timezone.now()

        refresh_record = (
            RefreshToken.objects
            .select_for_update()
            .select_related("user")
            .filter(
                jti=jti,
                revoked_at__isnull=True,
                expires_at__gt=now,
            )
            .first()
        )

        if refresh_record is None:
            return 401, {
                "detail": "Refresh token revoked or expired"
            }

        user = refresh_record.user

        if not user.is_active or payload.get("user_id") != user.pk:
            return 401, {"detail": "Invalid refresh token"}

        # 5. Revoke the old refresh token
        refresh_record.revoked_at = now
        refresh_record.save(update_fields=["revoked_at"])

        # 6. Generate a new access + refresh token pair
        tokens, new_jti, new_expires_at = generate_token_pair(user.id)

        # 7. Save the new refresh token's metadata
        RefreshToken.objects.create(
            user=user,
            jti=new_jti,
            expires_at=new_expires_at,
        )

    # 8. Return the new token pair
    return 200, tokens



@router.get("/profile", response=UserOut, auth=jwt_auth)
def profile(request):
    user = request.auth
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email
    }