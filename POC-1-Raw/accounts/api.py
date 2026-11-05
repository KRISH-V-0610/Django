from ninja import Router
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .schemas import RegisterSchema, UserOut

router = Router()


@router.post("/register", response={201: UserOut})
def register(request, data: RegisterSchema):
    User = get_user_model()

    # Validate password against Django's configured validators
    try:
        validate_password(data.password)
    except ValidationError as exc:
        return 400, {"detail": exc.messages}

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