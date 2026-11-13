from django.core.validators import MaxValueValidator
from django.db import models

from core.choices import PaymentMethod
from profiles.validators import validate_ico


class Partner(models.Model):
    """Odběratel/dodavatel. V 1. etapě patří právě jednomu fakturačnímu profilu."""

    profile = models.ForeignKey(
        "profiles.BillingProfile", on_delete=models.PROTECT, related_name="partners"
    )
    name = models.CharField("název / jméno", max_length=200)
    ico = models.CharField("IČO", max_length=8, blank=True, validators=[validate_ico])
    dic = models.CharField("DIČ", max_length=14, blank=True)
    vat_payer = models.BooleanField("plátce DPH", default=False)
    street = models.CharField("ulice a číslo", max_length=200, blank=True)
    city = models.CharField("obec", max_length=100, blank=True)
    zip_code = models.CharField("PSČ", max_length=10, blank=True)
    country = models.CharField("stát", max_length=60, default="Česká republika")
    billing_email = models.EmailField("e-mail pro fakturaci", blank=True)
    contact_person = models.CharField("kontaktní osoba", max_length=200, blank=True)
    phone = models.CharField("telefon", max_length=40, blank=True)
    is_customer = models.BooleanField("odběratel", default=True)
    is_supplier = models.BooleanField("dodavatel", default=False)
    is_active = models.BooleanField("aktivní", default=True)
    internal_note = models.TextField("interní poznámka", blank=True)
    due_days_override = models.PositiveSmallIntegerField(
        "vlastní splatnost (dny)", null=True, blank=True, validators=[MaxValueValidator(365)]
    )
    payment_method_override = models.CharField(
        "vlastní způsob úhrady", max_length=20, choices=PaymentMethod.choices, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["profile", "ico"])]

    def __str__(self):
        return self.name

    @property
    def address_lines(self):
        lines = [self.street, f"{self.zip_code} {self.city}".strip(), self.country]
        return [line for line in lines if line]
