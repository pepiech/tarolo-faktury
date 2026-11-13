from django.db import transaction

from audit import services as audit
from profiles.models import BillingProfile

from .models import ProfileMembership, User
from .permissions import require_admin


@transaction.atomic
def save_user(actor, user, membership_data, before=None):
    require_admin(actor.user)
    created = user.pk is None
    if not created:
        user.version += 1
    user.save()
    after = audit.snapshot(user, exclude=("last_login_at",))
    if created:
        audit.record(actor, "user.create", obj=user, after=after)
    else:
        b, a = audit.diff(before, after)
        if b or a:
            audit.record(actor, "user.update", obj=user, before=b, after=a)
    for profile_id, flags in membership_data.items():
        membership = ProfileMembership.objects.filter(user=user, profile_id=profile_id).first()
        old = {k: getattr(membership, k) for k in flags} if membership else {k: False for k in flags}
        if old == flags:
            continue
        if membership is None:
            membership = ProfileMembership(user=user, profile_id=profile_id)
        for key, value in flags.items():
            setattr(membership, key, value)
        if any(flags.values()):
            membership.save()
        elif membership.pk:
            ProfileMembership.objects.filter(pk=membership.pk).delete()
        audit.record(
            actor, "membership.change", obj=user, before=old, after=flags,
            profile=BillingProfile.objects.get(pk=profile_id),
        )
    return user


def ensure_admins(emails, actor=None):
    """Bootstrap: založí chybějící správce (nikoho nereaktivuje ani nemění)."""
    created = []
    for email in emails:
        if not User.objects.filter(email=email).exists():
            user = User.objects.create(email=email, name=email.split("@")[0], is_admin=True)
            audit.record(actor, "user.bootstrap_admin", obj=user, after=audit.snapshot(user))
            created.append(user)
    return created
