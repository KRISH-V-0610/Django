from django.conf import settings
from django.db import models


class IdempotencyRecord(models.Model):

    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    key = models.CharField(max_length=255)

    scope = models.CharField(max_length=255)

    request_hash = models.CharField(max_length=64)

    response_body = models.JSONField(
        null=True,
        blank=True,
    )

    response_status = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "scope", "key"],
                name="unique_user_scope_idempotency_key",
            )
        ]