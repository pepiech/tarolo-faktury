from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from audit import services as audit
from audit.forms import AuditFilterForm
from audit.models import AuditEvent
from audit.presentation import present
from core.concurrency import ConcurrentUpdate, lock_and_check
from core.decorators import admin_view
from profiles import services as profile_services
from profiles.models import BillingProfile

from . import services
from .forms import ProfileCreateForm, UserForm
from .models import User
from .permissions import actor_from_request


@admin_view
def users(request):
    rows = User.objects.prefetch_related("memberships__profile")
    return render(request, "accounts/users.html", {"users": rows})


@admin_view
def user_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else User()
    memberships = {m.profile_id: m for m in user.memberships.all()} if pk else {}
    if request.method == "POST":
        try:
            with transaction.atomic():
                if pk:
                    user = lock_and_check(User.objects, pk, request.POST.get("version"))
                before = audit.snapshot(user, exclude=("last_login_at",)) if pk else None
                form = UserForm(request.POST, instance=user, memberships=memberships)
                if form.is_valid():
                    services.save_user(
                        actor_from_request(request), form.save(commit=False),
                        form.membership_data(), before=before,
                    )
                    messages.success(request, "Uživatel uložen.")
                    return redirect("accounts:users")
        except ConcurrentUpdate as exc:
            messages.error(request, exc.message)
            return redirect(request.path)
    else:
        form = UserForm(instance=user, memberships=memberships)
    return render(request, "accounts/user_form.html", {"form": form, "edited": user})


@admin_view
def profiles(request):
    return render(request, "accounts/profiles.html", {"profiles": BillingProfile.objects.all()})


@admin_view
def profile_create(request):
    form = ProfileCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        profile = profile_services.create_profile(actor_from_request(request), form.save(commit=False))
        for warning in profile_services.duplicate_warnings(profile):
            messages.warning(request, warning)
        messages.success(
            request,
            "Profil založen s výchozími sazbami, režimy DPH a číselnou řadou. "
            "Doplňte bankovní účet a zkontrolujte nastavení.",
        )
        return redirect("profiles:settings", slug=profile.slug)
    if request.method == "POST":
        dic = request.POST.get("dic", "").strip()
        if dic and BillingProfile.objects.filter(dic__iexact=dic).exists():
            messages.warning(request, f"Profil se stejným DIČ {dic} už existuje.")
    return render(request, "accounts/profile_create.html", {"form": form})


@admin_view
def audit_log(request):
    form = AuditFilterForm(request.GET or None)
    events = form.filter(AuditEvent.objects.select_related("actor", "profile"))
    page = Paginator(events, 100).get_page(request.GET.get("strana"))
    now = timezone.localtime()
    fmt = "%Y-%m-%dT%H:%M"
    presets = [
        ("Poslední hodina", (now - timedelta(hours=1)).strftime(fmt)),
        ("Dnes", now.replace(hour=0, minute=0).strftime(fmt)),
        ("7 dní", (now - timedelta(days=7)).strftime(fmt)),
        ("30 dní", (now - timedelta(days=30)).strftime(fmt)),
    ]
    return render(request, "accounts/audit.html", {
        "form": form, "page": page, "views": present(page.object_list), "presets": presets,
        "filtered": any(v for k, v in request.GET.items() if k != "strana"),
    })
