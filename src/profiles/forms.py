from django import forms
from django.conf import settings
from django.utils import timezone

from .models import BankAccount, BillingProfile, NumberSeries, ReminderRule, VatRate, VatRegime


class VersionedForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and "version" in self.fields:
            self.fields["version"].initial = getattr(self.instance, "version", None)


class ProfileForm(VersionedForm):
    class Meta:
        model = BillingProfile
        fields = [
            "name", "legal_form", "street", "city", "zip_code", "country", "ico", "dic", "vat_payer",
            "register_note", "email", "phone", "web", "is_active",
            "default_due_days", "default_payment_method", "default_vat_regime",
            "default_bank_account", "default_series", "constant_symbol", "vs_rule",
            "rounding_mode", "vat_calc_method", "invoice_intro_text", "invoice_footer_text",
        ]
        widgets = {"invoice_footer_text": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = self.instance
        self.fields["default_vat_regime"].queryset = VatRegime.objects.filter(profile=p, is_active=True)
        self.fields["default_bank_account"].queryset = BankAccount.objects.filter(profile=p, is_active=True)
        self.fields["default_series"].queryset = NumberSeries.objects.filter(profile=p, is_active=True)


class BankAccountForm(VersionedForm):
    class Meta:
        model = BankAccount
        fields = ["label", "account_number", "iban", "bic", "bank_name", "is_active"]


class VatRateForm(forms.ModelForm):
    class Meta:
        model = VatRate
        fields = ["label", "rate_percent", "is_active", "order"]


class VatRegimeForm(forms.ModelForm):
    class Meta:
        model = VatRegime
        fields = ["code", "label", "applies_vat", "invoice_text", "is_active"]


class NumberSeriesForm(VersionedForm):
    class Meta:
        model = NumberSeries
        fields = ["name", "format", "start_value", "min_width", "is_active"]

    def __init__(self, *args, used=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.used = used
        if used:
            # Po prvním vystavení se formát ani start nemění; posun řeší samostatná operace.
            for name in ("format", "start_value", "min_width"):
                self.fields[name].disabled = True
                self.fields[name].help_text = "Řada už byla použita – změnit nelze. Použijte „Posunout další číslo“."


class ReminderRuleForm(forms.ModelForm):
    class Meta:
        model = ReminderRule
        fields = ["days_after_due", "label"]


class ShiftNumberForm(forms.Form):
    year = forms.IntegerField(label="rok (u ročních řad)", min_value=2000, max_value=2100)
    next_value = forms.IntegerField(label="další pořadové číslo", min_value=1)
    reason = forms.CharField(label="důvod", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["year"].initial = timezone.localdate().year


class LogoForm(forms.Form):
    logo = forms.FileField(label="logo (PNG nebo JPEG, max. 1 MB)")

    def clean_logo(self):
        logo = self.cleaned_data["logo"]
        name = logo.name.lower()
        if not name.endswith((".png", ".jpg", ".jpeg")):
            raise forms.ValidationError("Povolené jsou jen soubory PNG a JPEG.")
        if logo.size > settings.LOGO_MAX_BYTES:
            raise forms.ValidationError("Soubor je větší než 1 MB.")
        head = logo.read(8)
        logo.seek(0)
        if not (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8")):
            raise forms.ValidationError("Soubor není platný obrázek PNG/JPEG.")
        return logo
