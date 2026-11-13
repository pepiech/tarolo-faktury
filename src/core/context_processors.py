from django.conf import settings

from accounts.permissions import (
    INVOICES_MODIFY,
    INVOICES_VIEW,
    PARTNERS_MODIFY,
    accessible_profiles,
    has_perm,
)


def app(request):
    user = getattr(request, "user", None)
    profile = getattr(request, "profile", None)
    ctx = {
        "app_environment": settings.APP_ENVIRONMENT,
        "is_test_environment": settings.APP_ENVIRONMENT != "production",
        "current_user": user,
        "active_profile": profile,
    }
    if user is not None:
        ctx["nav_profiles"] = accessible_profiles(user).only("slug", "name", "is_active")
        if profile is not None:
            ctx["perm"] = {
                "invoices_view": has_perm(user, profile, INVOICES_VIEW),
                "invoices_modify": has_perm(user, profile, INVOICES_MODIFY),
                "partners_modify": has_perm(user, profile, PARTNERS_MODIFY),
                "admin": user.is_admin,
            }
    return ctx
