"""Admin activate/deactivate and permanent user purge (data + account)."""

from __future__ import annotations

import os
import shutil

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, QuerySet

from nodepoint.auth.account_lifecycle import AccountLifecycleError
from nodepoint.auth.users import User, ensure_profile, user_role
from nodepoint.enums import AccountStatus, UserRole
from nodepoint.models import ApiKey, UserProfile, Workspace
from nodepoint.services.workspace import workspace_storage_abspath
from typing import Any, Dict


def account_state_label(user) -> str:
    """UI-facing lifecycle: active | inactive | pending_deletion."""
    profile = ensure_profile(user)
    if profile.status == AccountStatus.PENDING_DELETION:
        return "pending_deletion"
    if profile.status != AccountStatus.ACTIVE:
        return profile.status
    return "active" if user.is_active else "inactive"


def user_list_annotations(qs: QuerySet) -> QuerySet:
    return qs.annotate(
        api_keys_total=Count("api_keys", distinct=True),
        api_keys_active=Count("api_keys", filter=Q(api_keys__is_active=True), distinct=True),
        workspaces_total=Count("workspaces", distinct=True),
    )


def can_permanently_purge(user) -> bool:
    profile = ensure_profile(user)
    if profile.status == AccountStatus.PURGED:
        return False
    if profile.status == AccountStatus.PENDING_DELETION:
        return True
    return profile.status == AccountStatus.ACTIVE and not user.is_active


def can_activate(user) -> bool:
    profile = ensure_profile(user)
    return profile.status == AccountStatus.ACTIVE and not user.is_active


def deactivate_user(user, *, requested_by) -> UserProfile:
    if user.pk == requested_by.pk:
        raise AccountLifecycleError("Cannot deactivate your own account.")
    if user_role(user) == UserRole.SUPERADMIN:
        raise AccountLifecycleError("Cannot deactivate a superadmin account.")
    profile = ensure_profile(user)
    if profile.status == AccountStatus.PENDING_DELETION:
        raise AccountLifecycleError(
            "Account is pending deletion; use purge or recover instead."
        )
    if profile.status != AccountStatus.ACTIVE:
        raise AccountLifecycleError(f"Cannot deactivate account in status '{profile.status}'.")
    user.is_active = False
    user.save(update_fields=["is_active"])
    ApiKey.objects.filter(user=user).update(is_active=False)
    return profile


def activate_user(user, *, requested_by) -> UserProfile:
    profile = ensure_profile(user)
    if profile.status == AccountStatus.PENDING_DELETION:
        raise AccountLifecycleError(
            "Use POST /api/auth/account/recover/ for pending deletion accounts."
        )
    if profile.status != AccountStatus.ACTIVE:
        raise AccountLifecycleError(f"Cannot activate account in status '{profile.status}'.")
    user.is_active = True
    user.save(update_fields=["is_active"])
    return profile


def _remove_workspace_media(workspace: Workspace) -> None:
    path = workspace_storage_abspath(workspace)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    owner_root = os.path.join(settings.MEDIA_ROOT, "workspaces", str(workspace.owner_id))
    if os.path.isdir(owner_root) and not os.listdir(owner_root):
        shutil.rmtree(owner_root, ignore_errors=True)
    legacy = os.path.join(settings.MEDIA_ROOT, "workspaces", workspace.name)
    if os.path.isdir(legacy):
        shutil.rmtree(legacy, ignore_errors=True)


@transaction.atomic
def purge_user_permanently(user: User, *, requested_by: User) -> Dict[str, Any]:
    """
    Hard-delete user and owned DB rows (CASCADE). Removes workspace media on disk.
    Not recoverable. Allowed for inactive accounts or pending_deletion.
    """
    if user.pk == requested_by.pk:
        raise AccountLifecycleError("Cannot purge your own account.")
    if user_role(user) == UserRole.SUPERADMIN:
        raise AccountLifecycleError("Cannot purge a superadmin account.")
    if not can_permanently_purge(user):
        raise AccountLifecycleError(
            "Permanent purge requires an inactive account "
            "(PATCH is_active=false) or pending_deletion status."
        )

    profile = ensure_profile(user)
    workspaces = list(Workspace.objects.filter(owner=user))
    for ws in workspaces:
        _remove_workspace_media(ws)

    summary = {
        "user_id": user.pk,
        "username": user.username,
        "workspaces_removed": len(workspaces),
        "purged": True,
        "recoverable": False,
    }
    uid = user.pk
    user.delete()
    summary["user_id"] = uid
    return summary


def filter_manageable_users(
    qs: QuerySet,
    *,
    role: str | None = None,
    account_state: str | None = None,
    is_active: bool | None = None,
    status: str | None = None,
    search: str | None = None,
) -> QuerySet:
    if role:
        qs = qs.filter(profile__role=role)
    if status:
        qs = qs.filter(profile__status=status)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    if account_state:
        state = account_state.strip().lower()
        if state == "active":
            qs = qs.filter(profile__status=AccountStatus.ACTIVE, is_active=True)
        elif state == "inactive":
            qs = qs.filter(profile__status=AccountStatus.ACTIVE, is_active=False)
        elif state == "pending_deletion":
            qs = qs.filter(profile__status=AccountStatus.PENDING_DELETION)
        else:
            raise ValueError(
                f"Invalid account_state '{account_state}'. "
                "Use active, inactive, or pending_deletion."
            )
    if search:
        qs = qs.filter(username__icontains=search.strip())
    return qs


def filter_api_keys(
    qs: QuerySet,
    *,
    is_active: bool | None = None,
    user_id: int | None = None,
    role: str | None = None,
    include_expired: bool = True,
) -> QuerySet:
    from django.utils import timezone

    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if role:
        qs = qs.filter(user__profile__role=role)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    if not include_expired:
        qs = qs.filter(expires_at__gt=timezone.now())
    return qs
