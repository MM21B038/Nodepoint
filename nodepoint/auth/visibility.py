"""Resource visibility for superadmin / admin / user hierarchy."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Q, QuerySet

from nodepoint.auth.users import user_role
from nodepoint.enums import UserRole
from nodepoint.models import Workspace, WorkspaceGroup

User = get_user_model()


class AccessDenied(PermissionError):
    pass


def visible_owner_ids(actor: AbstractBaseUser) -> list[int] | None:
    """Return owner user ids visible to actor, or None for unrestricted (superadmin)."""
    role = user_role(actor)
    if role == UserRole.SUPERADMIN:
        return None
    if role == UserRole.ADMIN:
        managed = list(
            User.objects.filter(
                profile__managed_by=actor, profile__role=UserRole.USER
            ).values_list("pk", flat=True)
        )
        return [actor.pk, *managed]
    return [actor.pk]


def visible_workspaces_qs(actor: AbstractBaseUser) -> QuerySet[Workspace]:
    from nodepoint.services.workspace_group import (
        GROUP_CHAT_PREFIX,
        LEGACY_FLAGGED_CHAT_WORKSPACE_NAME,
    )

    base = Workspace.objects.exclude(name__startswith=GROUP_CHAT_PREFIX).exclude(
        name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME
    )
    owner_ids = visible_owner_ids(actor)
    if owner_ids is None:
        return base
    return base.filter(owner_id__in=owner_ids)


def visible_groups_qs(actor: AbstractBaseUser) -> QuerySet[WorkspaceGroup]:
    owner_ids = visible_owner_ids(actor)
    if owner_ids is None:
        return WorkspaceGroup.objects.all()
    return WorkspaceGroup.objects.filter(owner_id__in=owner_ids)


def can_access_owner(actor: AbstractBaseUser, owner_id: int) -> bool:
    visible = visible_owner_ids(actor)
    if visible is None:
        return True
    return owner_id in visible


def can_access_workspace(actor: AbstractBaseUser, workspace: Workspace) -> bool:
    from nodepoint.services.workspace_group import (
        is_internal_chat_workspace_name,
        parse_group_chat_workspace_name,
    )

    if is_internal_chat_workspace_name(workspace.name):
        parsed = parse_group_chat_workspace_name(workspace.name)
        if parsed:
            owner_id, group_name = parsed
            try:
                group = WorkspaceGroup.objects.get(owner_id=owner_id, name=group_name)
            except WorkspaceGroup.DoesNotExist:
                return False
            return can_access_owner(actor, group.owner_id)
        group_name = workspace.name.removeprefix("__group_chat__")
        try:
            group = WorkspaceGroup.objects.get(name=group_name, owner_id=workspace.owner_id)
        except WorkspaceGroup.DoesNotExist:
            return False
        return can_access_owner(actor, group.owner_id)
    return can_access_owner(actor, workspace.owner_id)


def can_access_group(actor: AbstractBaseUser, group: WorkspaceGroup) -> bool:
    return can_access_owner(actor, group.owner_id)


def require_workspace_access(actor: AbstractBaseUser, workspace: Workspace) -> None:
    if not can_access_workspace(actor, workspace):
        raise AccessDenied("You do not have access to this workspace")


def require_group_access(actor: AbstractBaseUser, group: WorkspaceGroup) -> None:
    if not can_access_group(actor, group):
        raise AccessDenied("You do not have access to this group")


def allowed_workspace_names(actor: AbstractBaseUser) -> list[str]:
    return list(visible_workspaces_qs(actor).values_list("name", flat=True))


def allowed_workspace_ids(actor: AbstractBaseUser) -> list[int]:
    return list(visible_workspaces_qs(actor).values_list("pk", flat=True))


def can_manage_user(actor: AbstractBaseUser, target: AbstractBaseUser) -> bool:
    if actor.pk == target.pk:
        return True
    if user_role(actor) == UserRole.SUPERADMIN:
        return True
    if user_role(actor) == UserRole.ADMIN:
        profile = getattr(target, "profile", None)
        return profile is not None and profile.managed_by_id == actor.pk
    return False


def manageable_users_qs(actor: AbstractBaseUser) -> QuerySet:
    role = user_role(actor)
    if role == UserRole.SUPERADMIN:
        return User.objects.all()
    if role == UserRole.ADMIN:
        return User.objects.filter(Q(profile__managed_by=actor) | Q(pk=actor.pk))
    return User.objects.filter(pk=actor.pk)
