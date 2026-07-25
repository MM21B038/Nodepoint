from __future__ import annotations

from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from nodepoint.auth.users import User
from nodepoint.auth.visibility import can_manage_user
from nodepoint.models import ApiUsageLog
from typing import Any, Dict


def usage_summary_for_user(user: User, *, days: int = 30) -> Dict[str, Any]:
    since = timezone.now() - timedelta(days=days)
    qs = ApiUsageLog.objects.filter(user=user, created_at__gte=since)
    total = qs.count()
    by_scope = list(
        qs.values("scope")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    by_endpoint = list(
        qs.values("url_name")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    by_day = list(
        qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    return {
        "user_id": user.pk,
        "username": user.username,
        "period_days": days,
        "total_calls": total,
        "by_scope": by_scope,
        "by_endpoint": by_endpoint,
        "by_day": by_day,
    }


def usage_platform_summary(*, days: int = 30) -> Dict[str, Any]:
    since = timezone.now() - timedelta(days=days)
    qs = ApiUsageLog.objects.filter(created_at__gte=since)
    by_user = list(
        qs.values("user_id", "user__username")
        .annotate(count=Count("id"))
        .order_by("-count")[:50]
    )
    by_scope = list(
        qs.values("scope")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    return {
        "period_days": days,
        "total_calls": qs.count(),
        "by_user": by_user,
        "by_scope": by_scope,
    }


def assert_can_view_usage(actor, target) -> None:
    if not can_manage_user(actor, target) and actor.pk != target.pk:
        raise PermissionError("Cannot view usage for this user.")
