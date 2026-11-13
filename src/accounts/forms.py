from django import forms

from profiles.models import BillingProfile

from .models import User, normalize_email

PERM_FIELDS = [
    ("can_view_invoices", "invoices:view"),
    ("can_modify_invoices", "invoices:modify"),
    ("can_modify_partners", "partners:modify"),
]


class UserForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = User
        fields = ["email", "name", "is_active", "is_admin"]

    def __init__(self, *args, memberships=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profiles = list(BillingProfile.objects.all())
        memberships = memberships or {}
        if self.instance.pk:
            self.fields["version"].initial = self.instance.version
        for profile in self.profiles:
            current = memberships.get(profile.pk)
            for attr, label in PERM_FIELDS:
                self.fields[f"p{profile.pk}_{attr}"] = forms.BooleanField(
                    required=False, label=label,
                    initial=bool(current and getattr(current, attr)),
                )

    def clean_email(self):
        email = normalize_email(self.cleaned_data["email"])
        clash = User.objects.filter(email=email).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Uživatel s tímto e-mailem už existuje.")
        return email

    def permission_rows(self):
        for profile in self.profiles:
            yield profile, [self[f"p{profile.pk}_{attr}"] for attr, _ in PERM_FIELDS]

    def membership_data(self):
        return {
            profile.pk: {
                attr: self.cleaned_data.get(f"p{profile.pk}_{attr}", False)
                for attr, _ in PERM_FIELDS
            }
            for profile in self.profiles
        }


class ProfileCreateForm(forms.ModelForm):
    class Meta:
        model = BillingProfile
        fields = [
            "slug", "name", "legal_form", "street", "city", "zip_code", "country", "ico", "dic",
            "vat_payer", "email", "phone",
        ]
        help_texts = {"slug": "Krátký identifikátor do adresy, např. f13 nebo martin-piech."}
