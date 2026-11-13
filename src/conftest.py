import json
import os
import time
from datetime import date
from decimal import Decimal

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client

# Playwright (E2E) běží v asyncio smyčce; Django ORM v testovacím vlákně to jinak odmítá.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

TEST_KID = "test-kid"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key()))
TEST_JWKS = {"keys": [{**_jwk, "kid": TEST_KID, "alg": "RS256", "use": "sig"}]}


@pytest.fixture(autouse=True)
def _test_settings(settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.CF_ACCESS_STATIC_JWKS = TEST_JWKS
    return settings


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def make_token(email, *, aud=None, iss=None, exp_in=300, kid=TEST_KID, key=PRIVATE_PEM):
    from django.conf import settings

    now = int(time.time())
    claims = {
        "email": email,
        "aud": [aud or settings.CF_ACCESS_AUD],
        "iss": iss or f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
        "iat": now,
        "nbf": now,
        "exp": now + exp_in,
        "sub": "test",
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def client_for(user_or_email):
    email = getattr(user_or_email, "email", user_or_email)
    return Client(headers={"Cf-Access-Jwt-Assertion": make_token(email)})


def iban_for(bank_code, account, prefix="000000"):
    """Platný CZ IBAN z čísla účtu (pro testovací data)."""
    bban = f"{bank_code:0>4}{prefix:0>6}{account:0>10}"
    check = 98 - int("".join(str(int(c, 36)) for c in bban + "CZ00")) % 97
    return f"CZ{check:02d}{bban}"


# --- data ------------------------------------------------------------------------


@pytest.fixture
def make_user(db):
    from accounts.models import ProfileMembership, User

    def _make(email, *, admin=False, active=True, profiles=None):
        user = User.objects.create(email=email, name=email.split("@")[0], is_admin=admin,
                                   is_active=active)
        for profile, perms in (profiles or {}).items():
            ProfileMembership.objects.create(
                user=user, profile=profile,
                can_view_invoices="invoices:view" in perms,
                can_modify_invoices="invoices:modify" in perms,
                can_modify_partners="partners:modify" in perms,
            )
        return user

    return _make


@pytest.fixture
def make_profile(db):
    from profiles.models import BankAccount, BillingProfile
    from profiles.services import seed_defaults

    def _make(slug, ico, *, name=None, account="2201090240", bank="6210", **extra):
        profile = BillingProfile.objects.create(
            slug=slug, name=name or slug.upper(), street="Ulice 1", city="Ostrava",
            zip_code="70900", ico=ico, dic=f"CZ{ico}", **extra,
        )
        seed_defaults(profile)
        acc = BankAccount.objects.create(
            profile=profile, label="Hlavní", account_number=f"{account}/{bank}",
            iban=iban_for(bank, account), bic="BREXCZPP", bank_name="mBank",
        )
        profile.default_bank_account = acc
        profile.save()
        profile.refresh_from_db()
        return profile

    return _make


@pytest.fixture
def admin(make_user):
    return make_user("admin@test.cz", admin=True)


@pytest.fixture
def profile(make_profile):
    return make_profile("f13", "06646654", name="F13 CyberTech s.r.o.")


@pytest.fixture
def other_profile(make_profile):
    return make_profile("martin", "02963281", name="Martin Piech", account="1234567899", bank="0100")


@pytest.fixture
def make_partner(db):
    from partners.models import Partner

    def _make(profile, name="DvB-AF s.r.o.", **extra):
        return Partner.objects.create(
            profile=profile, name=name, ico=extra.pop("ico", "25834746"),
            dic=extra.pop("dic", "CZ25834746"), street="Meleček 227", city="Hradec nad Moravicí",
            zip_code="74741", **extra,
        )

    return _make


@pytest.fixture
def partner(profile, make_partner):
    return make_partner(profile)


@pytest.fixture
def actor(admin):
    from accounts.permissions import Actor

    return Actor(user=admin)


def header_for(profile, partner, **overrides):
    head = {
        "partner": partner,
        "series": profile.default_series,
        "vat_regime": profile.default_vat_regime,
        "issue_date": date(2026, 10, 15),
        "taxable_supply_date": date(2026, 9, 30),
        "due_date": date(2026, 10, 25),
        "payment_method": "bank_transfer",
        "bank_account": profile.default_bank_account,
        "variable_symbol": "",
        "constant_symbol": "",
        "specific_symbol": "",
        "order_reference": "",
        "public_note": "",
        "internal_note": "",
    }
    head.update(overrides)
    return head


def line(desc="Servisní práce", qty="1", price="1600.00", rate="21", discount="0", unit=""):
    return {
        "description": desc, "quantity": Decimal(qty), "unit": unit,
        "unit_price": Decimal(price), "discount_percent": Decimal(discount),
        "vat_rate_percent": Decimal(rate),
    }


@pytest.fixture
def draft(actor, profile, partner):
    from invoicing import services

    return services.create_draft(actor, profile, header_for(profile, partner), [line()])
