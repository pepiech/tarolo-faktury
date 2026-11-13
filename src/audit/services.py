from datetime import date, datetime
from decimal import Decimal

from django.db import models
from django.db.models.fields.files import FieldFile

from .models import AuditEvent

SECRET_FIELDS = {"password", "secret", "token"}


def _value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, FieldFile):
        return value.name or ""
    return value


def snapshot(instance, fields=None, exclude=()):
    """Slovník hodnot polí modelu vhodný pro audit (bez tajných údajů)."""
    if instance is None:
        return None
    data = {}
    for field in instance._meta.concrete_fields:
        name = field.attname if field.is_relation else field.name
        if fields is not None and field.name not in fields and name not in fields:
            continue
        if field.name in exclude or any(s in field.name for s in SECRET_FIELDS):
            continue
        data[name] = _value(getattr(instance, name))
    return data


def diff(before, after):
    """Jen změněná pole (pro přehlednější audit)."""
    if before is None or after is None:
        return before, after
    keys = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def record(actor, action, *, profile=None, obj=None, before=None, after=None, note=""):
    user = getattr(actor, "user", None)
    return AuditEvent.objects.create(
        actor=user if getattr(user, "pk", None) else None,
        interface=getattr(actor, "interface", "system"),
        request_id=getattr(actor, "request_id", ""),
        profile=profile,
        action=action,
        object_type=obj._meta.label_lower if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        before=before,
        after=after,
        note=note,
    )
