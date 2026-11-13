from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Case, DecimalField, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.choices import PaymentMethod
from profiles.validators import validate_ks, validate_ss, validate_vs

ZERO = Decimal("0.00")
MONEY = {"max_digits": 14, "decimal_places": 2}


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Návrh"
    ISSUED = "issued", "Vystaveno"
    CANCELLED = "cancelled", "Stornováno"


class DeliveryStatus(models.TextChoices):
    NOT_SENT = "not_sent", "Neodesláno"
    DELIVERED_OUTSIDE = "delivered_outside", "Předáno mimo aplikaci"


class PaymentState(models.TextChoices):
    UNPAID = "unpaid", "Neuhrazeno"
    PARTIAL = "partial", "Částečně uhrazeno"
    PAID = "paid", "Uhrazeno"
    OVERPAID = "overpaid", "Přeplaceno"


class InvoiceQuerySet(models.QuerySet):
    def for_profile(self, profile):
        return self.filter(profile=profile)

    def with_payment_state(self, today=None):
        today = today or timezone.localdate()
        paid = (
            Payment.objects.filter(invoice=OuterRef("pk"))
            .values("invoice")
            .annotate(s=Sum("amount"))
            .values("s")
        )
        money = DecimalField(**MONEY)
        return self.annotate(
            paid_amount=Coalesce(Subquery(paid, output_field=money), Value(ZERO), output_field=money),
        ).annotate(
            remaining_amount=F("total") - F("paid_amount"),
            payment_state=Case(
                When(paid_amount__lte=0, then=Value(PaymentState.UNPAID)),
                When(paid_amount__lt=F("total"), then=Value(PaymentState.PARTIAL)),
                When(paid_amount=F("total"), then=Value(PaymentState.PAID)),
                default=Value(PaymentState.OVERPAID),
                output_field=models.CharField(),
            ),
            is_overdue=Case(
                When(
                    Q(status=InvoiceStatus.ISSUED)
                    & Q(due_date__lt=today)
                    & Q(total__gt=F("paid_amount")),
                    then=Value(True),
                ),
                default=Value(False),
                output_field=models.BooleanField(),
            ),
        )


class Invoice(models.Model):
    profile = models.ForeignKey(
        "profiles.BillingProfile", on_delete=models.PROTECT, related_name="invoices"
    )
    document_type = models.CharField(max_length=20, default="invoice", editable=False)
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT, db_index=True
    )
    series = models.ForeignKey(
        "profiles.NumberSeries", verbose_name="číselná řada", on_delete=models.PROTECT,
        related_name="invoices",
    )
    number = models.CharField("číslo", max_length=32, null=True, blank=True)
    variable_symbol = models.CharField(
        "variabilní symbol", max_length=10, blank=True, validators=[validate_vs]
    )
    constant_symbol = models.CharField(
        "konstantní symbol", max_length=4, blank=True, validators=[validate_ks]
    )
    specific_symbol = models.CharField(
        "specifický symbol", max_length=10, blank=True, validators=[validate_ss]
    )
    partner = models.ForeignKey(
        "partners.Partner", verbose_name="odběratel", on_delete=models.PROTECT,
        related_name="invoices",
    )
    issue_date = models.DateField("datum vystavení")
    taxable_supply_date = models.DateField("datum uskutečnění zdanitelného plnění")
    due_date = models.DateField("datum splatnosti")
    payment_method = models.CharField(
        "forma úhrady", max_length=20, choices=PaymentMethod.choices,
        default=PaymentMethod.BANK_TRANSFER,
    )
    bank_account = models.ForeignKey(
        "profiles.BankAccount", verbose_name="bankovní účet", null=True, blank=True,
        on_delete=models.PROTECT, related_name="invoices",
    )
    vat_regime = models.ForeignKey(
        "profiles.VatRegime", verbose_name="režim DPH", on_delete=models.PROTECT,
        related_name="invoices",
    )
    order_reference = models.CharField("objednávka", max_length=100, blank=True)
    public_note = models.TextField("text pro odběratele", blank=True)
    internal_note = models.TextField("interní poznámka", blank=True)

    total_base = models.DecimalField(default=ZERO, **MONEY)
    total_vat = models.DecimalField(default=ZERO, **MONEY)
    rounding = models.DecimalField(default=ZERO, **MONEY)
    total = models.DecimalField(default=ZERO, **MONEY)

    issuer_snapshot = models.JSONField(default=dict, blank=True)
    partner_snapshot = models.JSONField(default=dict, blank=True)
    bank_snapshot = models.JSONField(default=dict, blank=True)
    vat_regime_snapshot = models.JSONField(default=dict, blank=True)
    vat_summary = models.JSONField(default=list, blank=True)

    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancel_reason = models.TextField(blank=True)

    pdf_file = models.CharField(max_length=255, blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    data_hash = models.CharField(max_length=64, blank=True)

    delivery_status = models.CharField(
        max_length=20, choices=DeliveryStatus.choices, default=DeliveryStatus.NOT_SENT
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.PROTECT, related_name="+"
    )
    version = models.PositiveIntegerField(default=1)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "series", "number"],
                condition=Q(number__isnull=False),
                name="uniq_invoice_number_in_series",
            ),
            models.CheckConstraint(
                condition=Q(status="draft") | Q(number__isnull=False),
                name="issued_invoice_has_number",
            ),
        ]
        indexes = [models.Index(fields=["profile", "status", "due_date"])]

    def __str__(self):
        return self.display_number

    @property
    def display_number(self):
        return self.number or f"Návrh #{self.pk}"

    @property
    def is_draft(self):
        return self.status == InvoiceStatus.DRAFT

    @property
    def is_issued(self):
        return self.status == InvoiceStatus.ISSUED


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField("popis plnění", max_length=300)
    quantity = models.DecimalField(
        "množství", max_digits=12, decimal_places=3, default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit = models.CharField("MJ", max_length=12, blank=True)
    unit_price = models.DecimalField("cena za MJ bez DPH", **MONEY)
    discount_percent = models.DecimalField(
        "sleva %", max_digits=5, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    vat_rate_percent = models.DecimalField("DPH %", max_digits=5, decimal_places=2, default=ZERO)
    line_base = models.DecimalField(default=ZERO, **MONEY)
    line_vat = models.DecimalField(default=ZERO, **MONEY)
    line_total = models.DecimalField(default=ZERO, **MONEY)

    class Meta:
        ordering = ["position", "id"]


class Payment(models.Model):
    """Úhrada. Nemění se ani nemaže; oprava = protioperace se zápornou částkou."""

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField("částka", **MONEY)
    paid_on = models.DateField("datum úhrady")
    method = models.CharField(
        "způsob", max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.BANK_TRANSFER
    )
    reference = models.CharField("reference", max_length=100, blank=True)
    note = models.CharField("poznámka", max_length=255, blank=True)
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    source = models.CharField(max_length=20, default="manual")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["paid_on", "id"]


class DeliveryRecord(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="deliveries")
    delivered_on = models.DateField("datum předání")
    note = models.CharField("poznámka", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["delivered_on", "id"]


class ReminderRecord(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="reminders")
    reminded_on = models.DateField("datum upomínky")
    method = models.CharField("způsob", max_length=60, blank=True)
    result_note = models.CharField("výsledek / poznámka", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["reminded_on", "id"]
