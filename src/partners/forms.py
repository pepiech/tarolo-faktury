from django import forms

from profiles.models import BillingProfile

from .models import Partner


class PartnerForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Partner
        fields = [
            "name", "ico", "dic", "vat_payer", "street", "city", "zip_code", "country",
            "billing_email", "contact_person", "phone", "is_customer", "is_supplier", "is_active",
            "due_days_override", "payment_method_override", "internal_note",
        ]
        widgets = {"internal_note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["version"].initial = self.instance.version


class CopyPartnerForm(forms.Form):
    target = forms.ModelChoiceField(label="cílový profil", queryset=BillingProfile.objects.none())

    def __init__(self, *args, exclude_profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = BillingProfile.objects.exclude(pk=exclude_profile.pk)
