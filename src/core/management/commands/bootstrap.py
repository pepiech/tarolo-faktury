from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.permissions import Actor
from accounts.services import ensure_admins
from audit import services as audit
from profiles.models import BillingProfile
from profiles.services import fill_missing_default_account, seed_defaults

INITIAL_PROFILES = [
    # Identifikační údaje ověřené v ARES 13. 11. 2026 (zadání kap. 1.1).
    # Bankovní účty se záměrně nezakládají – doplní a potvrdí je správce.
    {
        "slug": "f13", "name": "F13 CyberTech s.r.o.", "legal_form": "společnost s ručením omezeným",
        "street": "Sokola Tůmy 1099/1", "city": "Ostrava - Hulváky", "zip_code": "709 00",
        "ico": "06646654", "dic": "CZ06646654", "vat_payer": True,
    },
    {
        "slug": "martin-piech", "name": "Martin Piech", "legal_form": "fyzická osoba podnikající",
        "street": "Gudrichova 469/82", "city": "Opava - Kylešovice", "zip_code": "747 06",
        "ico": "02963281", "dic": "CZ8007115424", "vat_payer": True, "constant_symbol": "0308",
        "invoice_footer_text": "Úrok z prodlení činí 0,050 % za každý den prodlení platby.",
    },
]


class Command(BaseCommand):
    help = "Idempotentně založí výchozí data: správce z BOOTSTRAP_ADMIN_EMAILS a úvodní profily."

    @transaction.atomic
    def handle(self, *args, **options):
        actor = Actor(user=None, interface="system")
        admins = ensure_admins(settings.BOOTSTRAP_ADMIN_EMAILS, actor)
        for user in admins:
            self.stdout.write(f"bootstrap: založen správce {user.email}")
        if not BillingProfile.objects.exists():
            for data in INITIAL_PROFILES:
                profile = BillingProfile.objects.create(**data)
                seed_defaults(profile)
                audit.record(actor, "profile.bootstrap", profile=profile, obj=profile,
                             after=audit.snapshot(profile))
                self.stdout.write(f"bootstrap: založen profil {profile.name}")
        for profile in BillingProfile.objects.filter(default_bank_account__isnull=True):
            if fill_missing_default_account(profile):
                audit.record(actor, "profile.default_account", profile=profile, obj=profile,
                             after={"default_bank_account": profile.bank_accounts.get(is_active=True).pk})
                self.stdout.write(f"bootstrap: výchozí účet doplněn u profilu {profile.name}")
        self.stdout.write("bootstrap: hotovo")
