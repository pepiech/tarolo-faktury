from django import get_version
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render


def database_status():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version_num')::integer")
        return str(cursor.fetchone()[0] // 10000)


def home(request):
    try:
        postgres_version = database_status()
        database_ok = True
    except Exception:
        postgres_version = "nedostupné"
        database_ok = False

    return render(
        request,
        "core/home.html",
        {
            "database_ok": database_ok,
            "django_version": ".".join(get_version().split(".")[:2]),
            "postgres_version": postgres_version,
        },
        status=200 if database_ok else 503,
    )


def health(request):
    try:
        database_status()
    except Exception:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)

    return JsonResponse(
        {
            "status": "ok",
            "application": "helloapp",
            "database": "postgresql",
        }
    )
