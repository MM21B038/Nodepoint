from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone

from nodepoint.auth.users import User, ensure_profile, user_role
from nodepoint.enums import AccountStatus, UserRole
from nodepoint.models import ApiKey, UserProfile
from typing import Any, Dict, List


class AccountLifecycleError(ValueError):
    pass


def deletion_grace_period() -> timedelta:
    days = getattr(settings, "ACCOUNT_DELETION_GRACE_DAYS", 30)
    return timedelta(days=days)


def profile_allows_login(profile: UserProfile) -> bool:
    return profile.status == AccountStatus.ACTIVE and profile.user.is_active


def login_block_message(profile: UserProfile) -> str | None:
    if profile.status == AccountStatus.PENDING_DELETION:
        purge = profile.purge_scheduled_at
        return (
            f"Account is scheduled for deletion. "
            f"Recover before {purge.isoformat() if purge else 'purge date'}."
        )
    if profile.status == AccountStatus.PURGED:
        return "Account has been permanently deleted."
    if not profile.user.is_active:
        return "Account is inactive."
    return None


def schedule_user_deletion(user, *, requested_by) -> UserProfile:
    if user.pk == requested_by.pk and user_role(requested_by) == UserRole.SUPERADMIN:
        raise AccountLifecycleError("Superadmin cannot delete their own account.")
    profile = ensure_profile(user)
    if profile.status == AccountStatus.PENDING_DELETION:
        raise AccountLifecycleError("Account is already scheduled for deletion.")
    now = timezone.now()
    profile.status = AccountStatus.PENDING_DELETION
    profile.deletion_requested_at = now
    profile.purge_scheduled_at = now + deletion_grace_period()
    profile.deletion_requested_by = requested_by
    profile.save(
        update_fields=[
            "status",
            "deletion_requested_at",
            "purge_scheduled_at",
            "deletion_requested_by",
        ]
    )
    user.is_active = False
    user.save(update_fields=["is_active"])
    ApiKey.objects.filter(user=user).update(is_active=False)
    return profile


def recover_account(*, username: str, password: str, role: str) -> User:
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist as exc:
        raise AccountLifecycleError("Invalid username or password.") from exc
    if not check_password(password, user.password):
        raise AccountLifecycleError("Invalid username or password.")
    profile = ensure_profile(user)
    if profile.role != role:
        raise AccountLifecycleError("Invalid account type.")
    if profile.status != AccountStatus.PENDING_DELETION:
        raise AccountLifecycleError("Account is not pending deletion.")
    if profile.purge_scheduled_at and timezone.now() >= profile.purge_scheduled_at:
        raise AccountLifecycleError("Recovery window has expired.")
    profile.status = AccountStatus.ACTIVE
    profile.deletion_requested_at = None
    profile.purge_scheduled_at = None
    profile.deletion_requested_by = None
    profile.save(
        update_fields=[
            "status",
            "deletion_requested_at",
            "purge_scheduled_at",
            "deletion_requested_by",
        ]
    )
    user.is_active = True
    user.save(update_fields=["is_active"])
    return user


def deletion_status_payload(user: User) -> Dict[str, Any]:
    profile = ensure_profile(user)
    remaining_days = None
    if profile.purge_scheduled_at and profile.status == AccountStatus.PENDING_DELETION:
        delta = profile.purge_scheduled_at - timezone.now()
        remaining_days = max(0, delta.days)
    return {
        "status": profile.status,
        "deletion_requested_at": profile.deletion_requested_at,
        "purge_scheduled_at": profile.purge_scheduled_at,
        "days_until_purge": remaining_days,
    }


@transaction.atomic
def purge_due_accounts() -> List[int]:
    """Hard-delete users whose purge_scheduled_at has passed. Returns deleted user ids."""
    from nodepoint.auth.user_lifecycle import purge_user_permanently

    due_users = [
        profile.user
        for profile in UserProfile.objects.filter(
            status=AccountStatus.PENDING_DELETION,
            purge_scheduled_at__lte=timezone.now(),
        ).select_related("user")
    ]
    if not due_users:
        return []
    requested_by = User.objects.filter(username="system").first() or due_users[0]
    deleted_ids: List[int] = []
    for user in due_users:
        uid = user.pk
        purge_user_permanently(user, requested_by=requested_by)
        deleted_ids.append(uid)
    return deleted_ids
