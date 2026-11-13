from functools import wraps

from accounts.permissions import AccessDenied, accessible_profiles, require, require_admin
from profiles.models import BillingProfile


def profile_view(perm=None, admin=False):
    """Načte profil z URL a ověří oprávnění; bez přístupu 404 (neprozradí existenci)."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, slug, *args, **kwargs):
            profile = BillingProfile.objects.filter(slug=slug).first()
            if profile is None:
                raise AccessDenied("profile")
            if admin:
                require_admin(request.user)
            elif perm:
                require(request.user, profile, perm)
            elif not accessible_profiles(request.user).filter(pk=profile.pk).exists():
                raise AccessDenied("profile")
            request.profile = profile
            return view(request, profile, *args, **kwargs)

        return wrapper

    return decorator


def admin_view(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        require_admin(request.user)
        return view(request, *args, **kwargs)

    return wrapper
