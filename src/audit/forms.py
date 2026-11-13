from django import forms
from django.db import models

from accounts.models import User
from profiles.models import BillingProfile

from .models import AuditEvent
from .presentation import action_choices

DT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]


class AuditFilterForm(forms.Form):
    od = forms.DateTimeField(label="Od", required=False, input_formats=DT_FORMATS,
                             widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    do = forms.DateTimeField(label="Do", required=False, input_formats=DT_FORMATS,
                             widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    uzivatel = forms.ChoiceField(label="Uživatel", required=False)
    rozhrani = forms.ChoiceField(label="Rozhraní", required=False,
                                 choices=[("", "— všechna —")] + AuditEvent.INTERFACES)
    profil = forms.ChoiceField(label="Profil", required=False)
    akce = forms.ChoiceField(label="Akce", required=False)
    hledat = forms.CharField(label="Hledat", required=False, max_length=100,
                             widget=forms.TextInput(attrs={"placeholder": "ID objektu, poznámka, request ID"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        actor_ids = AuditEvent.objects.exclude(actor=None).values("actor_id").distinct()
        self.fields["uzivatel"].choices = (
            [("", "— všichni —"), ("system", "systém (bez uživatele)")]
            + [(str(u.pk), u.email) for u in User.objects.filter(pk__in=actor_ids).order_by("email")]
        )
        self.fields["profil"].choices = (
            [("", "— všechny —"), ("none", "bez profilu")]
            + [(str(p.pk), p.name) for p in BillingProfile.objects.order_by("name")]
        )
        existing = AuditEvent.objects.values_list("action", flat=True).distinct()
        self.fields["akce"].choices = [("", "— všechny —")] + action_choices(existing)

    def clean(self):
        data = super().clean()
        if data.get("od") and data.get("do") and data["od"] > data["do"]:
            self.add_error("do", "Konec musí být po začátku.")
        return data

    def filter(self, events):
        if not self.is_valid():
            return events
        d = self.cleaned_data
        if d.get("od"):
            events = events.filter(at__gte=d["od"])
        if d.get("do"):
            events = events.filter(at__lte=d["do"])
        if d.get("uzivatel") == "system":
            events = events.filter(actor=None)
        elif d.get("uzivatel"):
            events = events.filter(actor_id=d["uzivatel"])
        if d.get("rozhrani"):
            events = events.filter(interface=d["rozhrani"])
        if d.get("profil") == "none":
            events = events.filter(profile=None)
        elif d.get("profil"):
            events = events.filter(profile_id=d["profil"])
        if d.get("akce"):
            akce = d["akce"]
            events = events.filter(action__startswith=akce) if akce.endswith(".") else events.filter(action=akce)
        if d.get("hledat"):
            q = d["hledat"].strip()
            events = events.filter(models.Q(object_id=q) | models.Q(note__icontains=q) | models.Q(request_id=q)
                                   | models.Q(action__icontains=q))
        return events
