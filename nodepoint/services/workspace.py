from __future__ import annotations

import os

from django.conf import settings
from django.db.models import QuerySet

from nodepoint.models import Workspace

# Internal storage for the flagged-scope chat (not a user workspace).
FLAGGED_CHAT_WORKSPACE_NAME = "__flagged_chat__"

RESERVED_WORKSPACE_NAMES = frozenset(
    {"flagged", FLAGGED_CHAT_WORKSPACE_NAME},
)


def get_flagged_workspaces_qs() -> QuerySet[Workspace]:
    return (
        Workspace.objects.filter(is_flag=True)
        .exclude(name=FLAGGED_CHAT_WORKSPACE_NAME)
        .order_by("created_at")
    )


def list_starred_workspace_names() -> list[str]:
    return list(get_flagged_workspaces_qs().values_list("name", flat=True))


def count_flagged_workspaces() -> int:
    """Starred user workspaces (excludes internal __flagged_chat__)."""
    return get_flagged_workspaces_qs().count()


def flagged_workspaces_summary() -> dict:
    names = list(
        get_flagged_workspaces_qs().order_by("name").values_list("name", flat=True)
    )
    return {
        "count": len(names),
        "workspaces": names,
    }


def get_default_flagged_workspace() -> Workspace | None:
    return get_flagged_workspaces_qs().first()


def require_default_flagged_workspace() -> Workspace:
    workspace = get_default_flagged_workspace()
    if workspace is None:
        raise Workspace.DoesNotExist(
            "No starred workspace; create a workspace and set is_flag=true"
        )
    return workspace


def get_or_create_flagged_chat_workspace() -> Workspace:
    """System workspace holding the single flagged-scope conversation."""
    workspace, _created = Workspace.objects.get_or_create(
        name=FLAGGED_CHAT_WORKSPACE_NAME,
        defaults={"is_flag": False},
    )
    workspace_path = os.path.join(
        settings.MEDIA_ROOT,
        "workspaces",
        workspace.name,
    )
    os.makedirs(workspace_path, exist_ok=True)
    return workspace


def resolve_workspace_for_chat(workspace_name: str) -> Workspace | None:
    """Named workspace chat only (not flagged scope)."""
    if workspace_name in RESERVED_WORKSPACE_NAMES:
        return None
    return Workspace.objects.filter(name=workspace_name).first()


def is_reserved_workspace_name(name: str) -> bool:
    return name.strip() in RESERVED_WORKSPACE_NAMES
