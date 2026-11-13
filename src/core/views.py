from django import get_version
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render

from accounts.permissions import accessible_profiles


def database_status():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version_num')::integer")
        return str(cursor.fetchone()[0] // 10000)


def home(request):
    profiles = list(accessible_profiles(request.user))
    if len(profiles) == 1 and not request.user.is_admin:
        return redirect("reports:dashboard", slug=profiles[0].slug)
    return render(request, "core/home.html", {"profiles": profiles})


def deployed_revision():
    """Commit zapsaný deploy skriptem do <release>/REVISION; lokálně 'dev'."""
    revision_file = settings.BASE_DIR.parent / "REVISION"
    try:
        return revision_file.read_text(encoding="ascii").strip()[:40] or "dev"
    except OSError:
        return "dev"


def health(request):
    try:
        postgres = database_status()
    except Exception:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse(
        {
            "status": "ok",
            "application": "faktury",
            "environment": settings.APP_ENVIRONMENT,
            "revision": deployed_revision(),
            "database": "postgresql",
            "postgres_version": postgres,
            "django_version": get_version(),
        }
    )
