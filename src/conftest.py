import pytest


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Testy nepotřebují collectstatic manifest, který vzniká až při nasazení."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
