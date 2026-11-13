from decimal import Decimal

from django import forms
from django.forms import formset_factory
from django.utils import timezone

from core.choices import PaymentMethod
from partners.models import Partner
from profiles.models import BankAccount, NumberSeries, VatRegime, VsRule
from profiles.validators import validate_ks, validate_ss, validate_vs

from .models import DeliveryStatus, InvoiceStatus, PaymentState

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class InvoiceForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    partner = forms.ModelChoiceField(label="Odběratel", queryset=Partner.objects.none())
    series = forms.ModelChoiceField(label="Číselná řada", queryset=NumberSeries.objects.none())
    vat_regime = forms.ModelChoiceField(label="Režim DPH", queryset=VatRegime.objects.none())
    issue_date = forms.DateField(label="Datum vystavení", widget=DATE_WIDGET)
    taxable_supply_date = forms.DateField(label="Datum uskutečnění zdanitelného plnění", widget=DATE_WIDGET)
    due_date = forms.DateField(
        label="Datum splatnosti", widget=DATE_WIDGET, required=False,
        help_text="Prázdné = podle splatnosti odběratele nebo profilu.",
    )
    payment_method = forms.ChoiceField(label="Forma úhrady", choices=PaymentMethod.choices)
    bank_account = forms.ModelChoiceField(
        label="Bankovní účet", queryset=BankAccount.objects.none(), required=False
    )
    variable_symbol = forms.CharField(
        label="Variabilní symbol", max_length=10, required=False, validators=[validate_vs]
    )
    constant_symbol = forms.CharField(
        label="Konstantní symbol", max_length=4, required=False, validators=[validate_ks]
    )
    specific_symbol = forms.CharField(
        label="Specifický symbol", max_length=10, required=False, validators=[validate_ss]
    )
    order_reference = forms.CharField(label="Objednávka", max_length=100, required=False)
    public_note = forms.CharField(
        label="Text pro odběratele", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    internal_note = forms.CharField(
        label="Interní poznámka", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        partners = Partner.objects.filter(profile=profile, is_active=True, is_customer=True)
        initial_partner = self.initial.get("partner")
        if initial_partner is not None:
            partners = Partner.objects.filter(profile=profile).filter(
                pk__in=[*partners.values_list("pk", flat=True), getattr(initial_partner, "pk", initial_partner)]
            )
        self.fields["partner"].queryset = partners
        self.fields["series"].queryset = NumberSeries.objects.filter(profile=profile, is_active=True)
        self.fields["vat_regime"].queryset = VatRegime.objects.filter(profile=profile, is_active=True)
        self.fields["bank_account"].queryset = BankAccount.objects.filter(profile=profile, is_active=True)
        if profile.vs_rule == VsRule.INVOICE_NUMBER:
            self.fields["variable_symbol"].disabled = True
            self.fields["variable_symbol"].help_text = "Přidělí se z čísla faktury při vystavení."

    @classmethod
    def defaults(cls, profile, partner=None):
        today = timezone.localdate()
        return {
            "partner": partner,
            "series": profile.default_series,
            "vat_regime": profile.default_vat_regime,
            "issue_date": today,
            "taxable_supply_date": today,
            "payment_method": (partner.payment_method_override if partner and partner.payment_method_override
                               else profile.default_payment_method),
            "bank_account": profile.default_bank_account,
            "constant_symbol": profile.constant_symbol,
        }

    def header(self):
        data = dict(self.cleaned_data)
        data.pop("version", None)
        if self.profile.vs_rule == VsRule.INVOICE_NUMBER:
            data["variable_symbol"] = ""
        return data


class LineForm(forms.Form):
    description = forms.CharField(label="Popis plnění", max_length=300)
    quantity = forms.DecimalField(
        label="Množství", max_digits=12, decimal_places=3, min_value=Decimal("0.001"),
        initial=Decimal("1"),
    )
    unit = forms.CharField(label="MJ", max_length=12, required=False)
    unit_price = forms.DecimalField(label="Cena/MJ bez DPH", max_digits=14, decimal_places=2)
    discount_percent = forms.DecimalField(
        label="Sleva %", max_digits=5, decimal_places=2, min_value=0, max_value=100,
        required=False, initial=Decimal("0"),
    )
    vat_rate_percent = forms.TypedChoiceField(label="DPH %", coerce=Decimal, choices=())

    def __init__(self, *args, rate_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vat_rate_percent"].choices = rate_choices

    def line(self):
        data = dict(self.cleaned_data)
        data.pop("DELETE", None)
        data["discount_percent"] = data.get("discount_percent") or Decimal("0")
        data["unit"] = data.get("unit") or ""
        return data


BaseLineFormSet = formset_factory(LineForm, extra=0, can_delete=True, min_num=1, validate_min=True)


def rate_choices(profile):
    rates = profile.vat_rates.filter(is_active=True)
    return [(str(r.rate_percent), f"{r.rate_percent.normalize()} %") for r in rates] or [("0.00", "0 %")]


def line_formset(profile, data=None, initial=None):
    choices = rate_choices(profile)
    return BaseLineFormSet(
        data=data, initial=initial, prefix="lines", form_kwargs={"rate_choices": choices}
    )


class PaymentForm(forms.Form):
    amount = forms.DecimalField(label="Částka (Kč)", max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    paid_on = forms.DateField(label="Datum úhrady", widget=DATE_WIDGET, initial=timezone.localdate)
    method = forms.ChoiceField(label="Způsob", choices=PaymentMethod.choices)
    reference = forms.CharField(label="Reference", max_length=100, required=False)
    note = forms.CharField(label="Poznámka", max_length=255, required=False)


class DeliveryForm(forms.Form):
    delivered_on = forms.DateField(label="Datum předání", widget=DATE_WIDGET, initial=timezone.localdate)
    note = forms.CharField(label="Poznámka", max_length=255, required=False,
                           help_text="Např. „e-mailem z Outlooku“, „osobně“.")


class ReminderForm(forms.Form):
    reminded_on = forms.DateField(label="Datum upomínky", widget=DATE_WIDGET, initial=timezone.localdate)
    method = forms.CharField(label="Způsob", max_length=60, required=False)
    result_note = forms.CharField(label="Výsledek / poznámka", max_length=255, required=False)


class CancelForm(forms.Form):
    reason = forms.CharField(label="Důvod storna", widget=forms.Textarea(attrs={"rows": 2}))


PAYMENT_FILTERS = [("", "— úhrada —"), *PaymentState.choices, ("overdue", "Po splatnosti")]


class InvoiceFilterForm(forms.Form):
    q = forms.CharField(label="Číslo / odběratel", required=False)
    partner = forms.ModelChoiceField(label="Odběratel", queryset=Partner.objects.none(), required=False)
    status = forms.ChoiceField(label="Stav", choices=[("", "— stav —"), *InvoiceStatus.choices], required=False)
    payment = forms.ChoiceField(label="Úhrada", choices=PAYMENT_FILTERS, required=False)
    delivery = forms.ChoiceField(label="Předání", choices=[("", "— předání —"), *DeliveryStatus.choices],
                                 required=False)
    issued_from = forms.DateField(label="Vystaveno od", widget=DATE_WIDGET, required=False)
    issued_to = forms.DateField(label="Vystaveno do", widget=DATE_WIDGET, required=False)
    due_from = forms.DateField(label="Splatnost od", widget=DATE_WIDGET, required=False)
    due_to = forms.DateField(label="Splatnost do", widget=DATE_WIDGET, required=False)

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["partner"].queryset = Partner.objects.filter(profile=profile)

    def apply(self, qs):
        if not self.is_valid():
            return qs
        d = self.cleaned_data
        if d["q"]:
            qs = qs.filter(forms_q(d["q"]))
        if d["partner"]:
            qs = qs.filter(partner=d["partner"])
        if d["status"]:
            qs = qs.filter(status=d["status"])
        if d["payment"] == "overdue":
            qs = qs.filter(is_overdue=True)
        elif d["payment"]:
            qs = qs.filter(payment_state=d["payment"]).exclude(status=InvoiceStatus.DRAFT)
        if d["delivery"]:
            qs = qs.filter(delivery_status=d["delivery"])
        if d["issued_from"]:
            qs = qs.filter(issue_date__gte=d["issued_from"])
        if d["issued_to"]:
            qs = qs.filter(issue_date__lte=d["issued_to"])
        if d["due_from"]:
            qs = qs.filter(due_date__gte=d["due_from"])
        if d["due_to"]:
            qs = qs.filter(due_date__lte=d["due_to"])
        return qs


def forms_q(text):
    from django.db.models import Q

    return Q(number__icontains=text) | Q(partner__name__icontains=text) | Q(variable_symbol=text)
