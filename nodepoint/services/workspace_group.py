from __future__ import annotations

import os
import re

from django.conf import settings
from django.db.models import Count, QuerySet

from nodepoint.models import Workspace, WorkspaceGroup, WorkspaceGroupMembership
from nodepoint.services import optional_fields as opt

GROUP_CHAT_PREFIX = "__group_chat__"
LEGACY_FLAGGED_CHAT_WORKSPACE_NAME = "__flagged_chat__"

_GROUP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,254}$")


class GroupError(ValueError):
    pass


class GroupNotFoundError(GroupError):
    pass


def group_chat_workspace_name(group_name: str) -> str:
    return f"{GROUP_CHAT_PREFIX}{group_name}"


def is_internal_chat_workspace_name(name: str) -> bool:
    return name.startswith(GROUP_CHAT_PREFIX) or name == LEGACY_FLAGGED_CHAT_WORKSPACE_NAME


def normalize_tag(tag: str | None) -> str:
    try:
        return opt.normalize_tag(tag)
    except ValueError as exc:
        raise GroupError(str(exc)) from exc


def normalize_description(description: str | None) -> str:
    try:
        return opt.normalize_description(description)
    except ValueError as exc:
        raise GroupError(str(exc)) from exc


def optional_field_for_api(value: str) -> str | None:
    return opt.optional_field_for_api(value)


def group_tag_for_api(tag: str) -> str | None:
    return optional_field_for_api(tag)


def validate_group_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise GroupError("Group name is required")
    if not _GROUP_NAME_RE.match(cleaned):
        raise GroupError(
            "Group name must start with a letter or digit and contain only "
            "letters, digits, underscores, and hyphens"
        )
    return cleaned


def get_group_by_name(name: str) -> WorkspaceGroup:
    try:
        return WorkspaceGroup.objects.get(name=name)
    except WorkspaceGroup.DoesNotExist as exc:
        raise GroupNotFoundError(f"Group not found: {name}") from exc


def get_or_create_group(name: str) -> tuple[WorkspaceGroup, bool]:
    cleaned = validate_group_name(name)
    return WorkspaceGroup.objects.get_or_create(name=cleaned)


def create_group(
    name: str,
    *,
    tag: str | None = None,
    description: str | None = None,
) -> WorkspaceGroup:
    cleaned = validate_group_name(name)
    if WorkspaceGroup.objects.filter(name=cleaned).exists():
        raise GroupError(f"Group already exists: {cleaned}")
    return WorkspaceGroup.objects.create(
        name=cleaned,
        tag=normalize_tag(tag),
        description=normalize_description(description),
    )


def list_groups() -> list[dict]:
    rows = (
        WorkspaceGroup.objects.annotate(workspace_count=Count("memberships"))
        .order_by("name")
        .values("name", "tag", "description", "created_at", "workspace_count")
    )
    return [
        {
            "name": row["name"],
            "tag": optional_field_for_api(row["tag"]),
            "description": optional_field_for_api(row["description"]),
            "workspace_count": row["workspace_count"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_group_detail(
    name: str,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    from nodepoint.services.workspace_catalog import paginate_queryset

    group = get_group_by_name(name)
    memberships = (
        WorkspaceGroupMembership.objects.filter(group=group)
        .select_related("workspace")
        .order_by("workspace__name")
    )
    total_count = memberships.count()
    page_rows, pagination = paginate_queryset(
        memberships, page=page, page_size=page_size
    )
    workspaces = [
        {
            "name": m.workspace.name,
            "created_at": m.workspace.created_at,
        }
        for m in page_rows
    ]
    return {
        "name": group.name,
        "tag": optional_field_for_api(group.tag),
        "description": optional_field_for_api(group.description),
        "created_at": group.created_at,
        "workspace_count": total_count,
        "workspaces": workspaces,
        "pagination": pagination,
    }


def group_workspaces_summary(group_name: str) -> dict:
    names = list_group_workspace_names(group_name)
    return {"group": group_name, "count": len(names), "workspaces": names}


def add_workspace_to_group(group_name: str, workspace: Workspace) -> None:
    if is_internal_chat_workspace_name(workspace.name):
        raise GroupError("Internal chat workspaces cannot be added to a group")
    group = get_group_by_name(group_name)
    WorkspaceGroupMembership.objects.get_or_create(group=group, workspace=workspace)


def remove_workspace_from_group(group_name: str, workspace: Workspace) -> None:
    group = get_group_by_name(group_name)
    deleted, _ = WorkspaceGroupMembership.objects.filter(
        group=group, workspace=workspace
    ).delete()
    if not deleted:
        raise GroupError(f"Workspace {workspace.name} is not in group {group_name}")


def delete_group(name: str) -> None:
    group = get_group_by_name(name)
    WorkspaceGroupMembership.objects.filter(group=group).delete()
    group.delete()


def get_group_workspaces_qs(group_name: str) -> QuerySet[Workspace]:
    get_group_by_name(group_name)
    return (
        Workspace.objects.filter(group_memberships__group__name=group_name)
        .exclude(name__startswith=GROUP_CHAT_PREFIX)
        .exclude(name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME)
        .order_by("created_at")
        .distinct()
    )


def list_group_workspace_names(group_name: str) -> list[str]:
    return list(
        get_group_workspaces_qs(group_name).values_list("name", flat=True)
    )


def get_or_create_group_chat_workspace(group_name: str) -> Workspace:
    get_group_by_name(group_name)
    chat_name = group_chat_workspace_name(group_name)
    workspace, _created = Workspace.objects.get_or_create(name=chat_name)
    workspace_path = os.path.join(
        settings.MEDIA_ROOT,
        "workspaces",
        workspace.name,
    )
    os.makedirs(workspace_path, exist_ok=True)
    return workspace


def get_default_upload_workspace() -> Workspace | None:
    """First user workspace by created_at (for uploads without workspace_name)."""
    return user_workspaces_qs().order_by("created_at").first()


def user_workspaces_qs() -> QuerySet[Workspace]:
    return (
        Workspace.objects.exclude(name__startswith=GROUP_CHAT_PREFIX)
        .exclude(name=LEGACY_FLAGGED_CHAT_WORKSPACE_NAME)
    )
