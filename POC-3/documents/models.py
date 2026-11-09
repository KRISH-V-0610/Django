from django.conf import settings
from django.db import models


class Document(models.Model):
    """A private document. Integer ids on purpose: easy to guess, which makes IDOR easy to demo."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="documents"
    )
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} (owner {self.owner_id})"
