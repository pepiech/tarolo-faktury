from django.db import models


class AuditEvent(models.Model):
    """Neměnný auditní záznam. UPDATE/DELETE blokuje databázový trigger."""

    INTERFACES = [("web", "web"), ("mcp", "MCP"), ("system", "systém"), ("cli", "příkazová řádka")]

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    interface = models.CharField(max_length=10, choices=INTERFACES, default="web")
    profile = models.ForeignKey(
        "profiles.BillingProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=40, blank=True, db_index=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    note = models.TextField(blank=True)
    request_id = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.object_type}#{self.object_id}"
