import uuid
from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from .cf_access import AccessTokenError, verify_access_jwt
from .models import User, normalize_email
from .permissions import AccessDenied

EXEMPT_PREFIXES = ("/health/", "/static/")
LAST_LOGIN_THROTTLE = timedelta(minutes=5)


class CloudflareAccessMiddleware:
    """Každý požadavek (kromě health checku) musí nést platný Cloudflare Access JWT
    a e-mail z něj musí patřit aktivnímu uživateli aplikace. Fail-closed."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid.uuid4().hex[:16]
        request.user = None
        if request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)

        token = request.headers.get("Cf-Access-Jwt-Assertion", "")
        if not token:
            return self._deny(request, "Chybí ověření Cloudflare Access.")
        try:
            claims = verify_access_jwt(token)
        except AccessTokenError:
            return self._deny(request, "Ověření Cloudflare Access selhalo.")

        email = normalize_email(claims.get("email"))
        user = User.objects.filter(email=email, is_active=True).first() if email else None
        if user is None:
            return self._deny(request, "Váš účet nemá do aplikace přístup.", email=email)

        now = timezone.now()
        if user.last_login_at is None or now - user.last_login_at > LAST_LOGIN_THROTTLE:
            User.objects.filter(pk=user.pk).update(last_login_at=now)
            user.last_login_at = now
        request.user = user
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, AccessDenied):
            return render(request, "core/not_found.html", status=404)
        return None

    @staticmethod
    def _deny(request, reason, email=""):
        return render(
            request, "core/forbidden.html", {"reason": reason, "email": email}, status=403
        )
