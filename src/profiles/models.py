from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.choices import PaymentMethod

from . import numbering
from .validators import normalize_iban, validate_bic, validate_iban, validate_ico, validate_ks


class VsRule(models.TextChoices):
    INVOICE_NUMBER = "invoice_number_digits", "Číslice z čísla faktury"
    MANUAL = "manual", "Zadává uživatel"


class RoundingMode(models.TextChoices):
    NONE = "none", "Bez zaokrouhlení"
    WHOLE_CZK = "whole_czk", "Na celé koruny"


class VatCalcMethod(models.TextChoices):
    PER_LINE = "per_line", "Po položkách (součet DPH řádků)"
    PER_RATE_TOTAL = "per_rate_total", "Ze součtu základů za sazbu"


def logo_upload_to(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"profiles/{instance.pk or 'new'}/logo.{ext}"


class BillingProfile(models.Model):
    """Vystavující subjekt (fakturační profil)."""

    slug = models.SlugField("zkratka v adrese", max_length=40, unique=True)
    name = models.CharField("obchodní firma / jméno", max_length=200)
    legal_form = models.CharField("právní forma", max_length=100, blank=True)
    street = models.CharField("ulice a číslo", max_length=200)
    city = models.CharField("obec", max_length=100)
    zip_code = models.CharField("PSČ", max_length=10)
    country = models.CharField("stát", max_length=60, default="Česká republika")
    ico = models.CharField("IČO", max_length=8, unique=True, validators=[validate_ico])
    dic = models.CharField("DIČ", max_length=14, blank=True)
    vat_payer = models.BooleanField("plátce DPH", default=True)
    register_note = models.CharField(
        "zápis v rejstříku", max_length=255, blank=True,
        help_text="Např. „zapsaná v obchodním rejstříku vedeném Krajským soudem v Ostravě, oddíl C, vložka …“",
    )
    email = models.EmailField("e-mail", blank=True)
    phone = models.CharField("telefon", max_length=40, blank=True)
    web = models.CharField("web", max_length=200, blank=True)
    logo = models.FileField("logo", upload_to=logo_upload_to, blank=True)
    is_active = models.BooleanField("aktivní pro nové doklady", default=True)

    default_due_days = models.PositiveSmallIntegerField(
        "výchozí splatnost (dny)", default=14, validators=[MaxValueValidator(365)]
    )
    default_payment_method = models.CharField(
        "výchozí způsob úhrady", max_length=20, choices=PaymentMethod.choices,
        default=PaymentMethod.BANK_TRANSFER,
    )
    default_vat_regime = models.ForeignKey(
        "VatRegime", verbose_name="výchozí režim DPH", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    default_bank_account = models.ForeignKey(
        "BankAccount", verbose_name="výchozí bankovní účet", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    default_series = models.ForeignKey(
        "NumberSeries", verbose_name="výchozí číselná řada", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    constant_symbol = models.CharField(
        "konstantní symbol", max_length=4, blank=True, validators=[validate_ks]
    )
    vs_rule = models.CharField(
        "variabilní symbol", max_length=30, choices=VsRule.choices, default=VsRule.INVOICE_NUMBER
    )
    rounding_mode = models.CharField(
        "zaokrouhlení celkové částky", max_length=20, choices=RoundingMode.choices,
        default=RoundingMode.NONE,
    )
    vat_calc_method = models.CharField(
        "výpočet DPH", max_length=20, choices=VatCalcMethod.choices,
        default=VatCalcMethod.PER_LINE,
    )
    invoice_intro_text = models.CharField(
        "úvodní text faktury", max_length=200, blank=True, default="Fakturujeme Vám"
    )
    invoice_footer_text = models.TextField("text v patičce faktury", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]
        verbose_name = "fakturační profil"
        verbose_name_plural = "fakturační profily"

    def __str__(self):
        return self.name

    @property
    def address_lines(self):
        return [self.street, f"{self.zip_code} {self.city}".strip(), self.country]


class BankAccount(models.Model):
    profile = models.ForeignKey(BillingProfile, on_delete=models.PROTECT, related_name="bank_accounts")
    label = models.CharField("označení", max_length=100)
    account_number = models.CharField(
        "číslo účtu", max_length=40, help_text="Tuzemský tvar pro zobrazení, např. 670100-2201090240/6210"
    )
    iban = models.CharField("IBAN", max_length=34, validators=[validate_iban])
    bic = models.CharField("BIC/SWIFT", max_length=11, blank=True, validators=[validate_bic])
    bank_name = models.CharField("banka", max_length=100, blank=True)
    is_active = models.BooleanField("aktivní", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return f"{self.label} ({self.account_number})"

    def save(self, *args, **kwargs):
        self.iban = normalize_iban(self.iban)
        self.bic = (self.bic or "").replace(" ", "").upper()
        super().save(*args, **kwargs)


class VatRate(models.Model):
    profile = models.ForeignKey(BillingProfile, on_delete=models.PROTECT, related_name="vat_rates")
    label = models.CharField("označení", max_length=60)
    rate_percent = models.DecimalField(
        "sazba %", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    is_active = models.BooleanField("aktivní", default=True)
    order = models.PositiveSmallIntegerField("pořadí", default=0)

    class Meta:
        ordering = ["order", "-rate_percent"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "rate_percent"], name="uniq_vat_rate")
        ]

    def __str__(self):
        return f"{self.label} ({self.rate_percent.normalize()} %)"


class VatRegime(models.Model):
    """Režim DPH celé faktury s textem, který se tiskne na doklad."""

    profile = models.ForeignKey(BillingProfile, on_delete=models.PROTECT, related_name="vat_regimes")
    code = models.SlugField("kód", max_length=40)
    label = models.CharField("název", max_length=100)
    applies_vat = models.BooleanField("počítá se DPH", default=True)
    invoice_text = models.CharField("text na faktuře", max_length=255, blank=True)
    is_active = models.BooleanField("aktivní", default=True)

    class Meta:
        ordering = ["label"]
        constraints = [models.UniqueConstraint(fields=["profile", "code"], name="uniq_vat_regime")]

    def __str__(self):
        return self.label


class DocumentType(models.TextChoices):
    INVOICE = "invoice", "Faktura"


class NumberSeries(models.Model):
    profile = models.ForeignKey(BillingProfile, on_delete=models.PROTECT, related_name="series")
    document_type = models.CharField(
        "typ dokladu", max_length=20, choices=DocumentType.choices, default=DocumentType.INVOICE
    )
    name = models.CharField("název", max_length=100)
    format = models.CharField(
        "formát", max_length=40, validators=[numbering.validate_format],
        help_text="{N} = pořadové číslo, {YY} = rok dvěma číslicemi, {YYYY} = rok. Např. {YY}{N}",
    )
    start_value = models.PositiveIntegerField("počáteční hodnota", default=1)
    min_width = models.PositiveSmallIntegerField(
        "minimální počet číslic pořadí", default=0, validators=[MaxValueValidator(10)]
    )
    is_active = models.BooleanField("aktivní", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]
        verbose_name = "číselná řada"
        verbose_name_plural = "číselné řady"

    def __str__(self):
        return f"{self.name} ({self.format})"

    def preview(self, year, n=None):
        return numbering.render(
            self.format, self.start_value if n is None else n, year, self.min_width
        )


class NumberCounter(models.Model):
    """Poslední přidělené pořadí v řadě a období (rok u ročních řad, '' u průběžných)."""

    series = models.ForeignKey(NumberSeries, on_delete=models.PROTECT, related_name="counters")
    period = models.CharField(max_length=4, blank=True)
    last_value = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["series", "period"], name="uniq_counter_period")
        ]


class ReminderRule(models.Model):
    profile = models.ForeignKey(BillingProfile, on_delete=models.PROTECT, related_name="reminder_rules")
    days_after_due = models.PositiveSmallIntegerField("dnů po splatnosti")
    label = models.CharField("označení", max_length=60)

    class Meta:
        ordering = ["days_after_due"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "days_after_due"], name="uniq_reminder_rule")
        ]

    def __str__(self):
        return f"{self.label} ({self.days_after_due} dnů)"
