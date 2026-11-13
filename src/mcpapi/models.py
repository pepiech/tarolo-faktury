from django.db import models


class IdempotencyRecord(models.Model):
    """Výsledek zapisovacího MCP volání pro daný client_request_id (opakování nevytvoří duplicitu)."""

    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    tool = models.CharField(max_length=60)
    client_request_id = models.CharField(max_length=120)
    request_hash = models.CharField(max_length=64)
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "tool", "client_request_id"], name="uniq_mcp_idempotency"
            )
        ]
