from django.db import models


def normalize_email(value):
    return (value or "").strip().lower()


class User(models.Model):
    """Uživatel aplikace. Identitu ověřuje Cloudflare Access, aplikace nemá hesla."""

    email = models.EmailField("e-mail", unique=True)
    name = models.CharField("jméno", max_length=200)
    is_active = models.BooleanField("aktivní", default=True)
    is_admin = models.BooleanField("globální správce", default=False)
    last_login_at = models.DateTimeField("poslední přihlášení", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    is_authenticated = True
    is_anonymous = False

    class Meta:
        ordering = ["name", "email"]
        verbose_name = "uživatel"
        verbose_name_plural = "uživatelé"

    def __str__(self):
        return self.name or self.email

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


class ProfileMembership(models.Model):
    """Oprávnění uživatele v jednom fakturačním profilu."""

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="memberships")
    profile = models.ForeignKey(
        "profiles.BillingProfile", on_delete=models.PROTECT, related_name="memberships"
    )
    can_view_invoices = models.BooleanField("invoices:view", default=False)
    can_modify_invoices = models.BooleanField("invoices:modify", default=False)
    can_modify_partners = models.BooleanField("partners:modify", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "profile"], name="uniq_membership")
        ]

    @property
    def has_any(self):
        return self.can_view_invoices or self.can_modify_invoices or self.can_modify_partners

    def perms(self):
        result = []
        if self.can_view_invoices:
            result.append("invoices:view")
        if self.can_modify_invoices:
            result.append("invoices:modify")
        if self.can_modify_partners:
            result.append("partners:modify")
        return result
