"""Scénář 1: přihlášení přes Cloudflare Access, neznámý/deaktivovaný uživatel nemá přístup."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client

from conftest import client_for, make_token

pytestmark = pytest.mark.django_db


def test_health_needs_no_login():
    response = Client().get("/health/")
    assert response.status_code == 200
    assert response.json()["application"] == "faktury"


def test_missing_token_is_forbidden(admin):
    assert Client().get("/").status_code == 403


def test_known_active_user_gets_in(admin):
    response = client_for(admin).get("/")
    assert response.status_code == 200
    admin.refresh_from_db()
    assert admin.last_login_at is not None


def test_email_is_normalized(admin):
    response = Client(headers={"Cf-Access-Jwt-Assertion": make_token("  ADMIN@Test.CZ ")}).get("/")
    assert response.status_code == 200


def test_unknown_email_is_forbidden_and_not_created(admin):
    from accounts.models import User

    response = client_for("stranger@test.cz").get("/")
    assert response.status_code == 403
    assert "nemá do aplikace přístup" in response.content.decode()
    assert not User.objects.filter(email="stranger@test.cz").exists()


def test_deactivated_user_is_forbidden(make_user):
    user = make_user("old@test.cz", admin=True, active=False)
    assert client_for(user).get("/").status_code == 403


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"aud": "jina-aplikace"},
        {"iss": "https://cizi.cloudflareaccess.com"},
        {"exp_in": -3600},
        {"kid": "neznamy-klic"},
    ],
)
def test_invalid_tokens_are_rejected(admin, token_kwargs):
    token = make_token(admin.email, **token_kwargs)
    assert Client(headers={"Cf-Access-Jwt-Assertion": token}).get("/").status_code == 403


def test_token_signed_by_foreign_key_is_rejected(admin):
    foreign = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    token = make_token(admin.email, key=foreign)
    assert Client(headers={"Cf-Access-Jwt-Assertion": token}).get("/").status_code == 403


def test_spoofed_email_header_alone_is_ignored(admin):
    response = Client(headers={"Cf-Access-Authenticated-User-Email": admin.email}).get("/")
    assert response.status_code == 403


def test_deployed_settings_refuse_static_jwks(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    import config.settings as s

    monkeypatch.setenv("APP_ENVIRONMENT", "staging")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "x.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "aud")
    monkeypatch.setenv("CF_ACCESS_STATIC_JWKS", '{"keys": []}')
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(s)
    monkeypatch.delenv("CF_ACCESS_STATIC_JWKS")
    monkeypatch.delenv("CF_ACCESS_AUD")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(s)
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    importlib.reload(s)
