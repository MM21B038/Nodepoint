from __future__ import annotations

from django.db.models import QuerySet

from nodepoint.models import Workspace
from nodepoint.services.workspace_group import (
    GROUP_CHAT_PREFIX,
    LEGACY_FLAGGED_CHAT_WORKSPACE_NAME,
    get_default_upload_workspace,
    is_internal_chat_workspace_name,
    user_workspaces_qs,
)

# Legacy alias used by catalog exclusions.
FLAGGED_CHAT_WORKSPACE_NAME = LEGACY_FLAGGED_CHAT_WORKSPACE_NAME


def resolve_workspace_for_chat(workspace_name: str) -> Workspace | None:
    """Named workspace chat only (not group scope)."""
    name = workspace_name.strip()
    if is_internal_chat_workspace_name(name):
        return None
    return Workspace.objects.filter(name=name).first()


def is_reserved_workspace_name(name: str) -> bool:
    cleaned = name.strip()
    if cleaned.startswith(GROUP_CHAT_PREFIX):
        return True
    return cleaned == LEGACY_FLAGGED_CHAT_WORKSPACE_NAME


def require_default_upload_workspace() -> Workspace:
    workspace = get_default_upload_workspace()
    if workspace is None:
        raise Workspace.DoesNotExist(
            "No workspace exists; create a workspace or pass workspace_name"
        )
    return workspace
