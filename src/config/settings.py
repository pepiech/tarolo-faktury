import json
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "development")
if APP_ENVIRONMENT not in {"development", "staging", "production"}:
    raise ImproperlyConfigured(f"Neznámé APP_ENVIRONMENT: {APP_ENVIRONMENT}")
IS_DEPLOYED = APP_ENVIRONMENT in {"staging", "production"}

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = (not IS_DEPLOYED) and os.environ.get("DJANGO_DEBUG", "") == "1"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# --- Cloudflare Access -------------------------------------------------------
CF_ACCESS_TEAM_DOMAIN = os.environ.get(
    "CF_ACCESS_TEAM_DOMAIN", "" if IS_DEPLOYED else "dev.cloudflareaccess.test"
)
CF_ACCESS_AUD = os.environ.get("CF_ACCESS_AUD", "" if IS_DEPLOYED else "dev-audience")
# Pevná sada veřejných klíčů (JWKS) – pouze pro vývoj a testy, nikdy na serveru.
_static_jwks = os.environ.get("CF_ACCESS_STATIC_JWKS", "")
CF_ACCESS_STATIC_JWKS = json.loads(_static_jwks) if _static_jwks else None
CF_ACCESS_JWKS_TTL = 3600

if IS_DEPLOYED:
    if not CF_ACCESS_TEAM_DOMAIN or not CF_ACCESS_AUD:
        raise ImproperlyConfigured("Chybí CF_ACCESS_TEAM_DOMAIN nebo CF_ACCESS_AUD.")
    if CF_ACCESS_STATIC_JWKS:
        raise ImproperlyConfigured("CF_ACCESS_STATIC_JWKS je povolené jen ve vývoji.")

BOOTSTRAP_ADMIN_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("BOOTSTRAP_ADMIN_EMAILS", "").split(",")
    if e.strip()
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "audit",
    "profiles",
    "partners",
    "invoicing",
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "accounts.middleware.CloudflareAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app",
            ],
            "builtins": ["core.templatetags.cz"],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "helloapp"),
        "USER": os.environ.get("POSTGRES_USER", "helloapp"),
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

LANGUAGE_CODE = "cs"
TIME_ZONE = "Europe/Prague"
USE_I18N = True
USE_TZ = True

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR.parent / "media"))
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
LOGO_MAX_BYTES = 1024 * 1024

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = IS_DEPLOYED
CSRF_COOKIE_SECURE = IS_DEPLOYED
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"weasyprint": {"level": "ERROR"}, "fontTools": {"level": "ERROR"}},
}
