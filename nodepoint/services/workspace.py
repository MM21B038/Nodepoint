from __future__ import annotations

import os
import shutil

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet

from nodepoint.models import Workspace
from nodepoint.quadrant.manager import rename_workspace_vectors
from nodepoint.services import optional_fields as opt
from nodepoint.services.workspace_group import (
    GROUP_CHAT_PREFIX,
    LEGACY_FLAGGED_CHAT_WORKSPACE_NAME,
    get_default_upload_workspace,
    is_internal_chat_workspace_name,
    user_workspaces_qs,
)

# Legacy alias used by catalog exclusions.
FLAGGED_CHAT_WORKSPACE_NAME = LEGACY_FLAGGED_CHAT_WORKSPACE_NAME


class WorkspaceValidationError(ValueError):
    pass


class WorkspaceNotFoundError(WorkspaceValidationError):
    pass


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


def create_workspace(
    name: str,
    *,
    tag: str | None = None,
    description: str | None = None,
) -> Workspace:
    if not name:
        raise WorkspaceValidationError("Workspace name required")
    if is_reserved_workspace_name(name):
        raise WorkspaceValidationError(f"Workspace name '{name.strip()}' is reserved")
    try:
        normalized_tag = opt.normalize_tag(tag)
        normalized_description = opt.normalize_description(description)
    except ValueError as exc:
        raise WorkspaceValidationError(str(exc)) from exc
    return Workspace.objects.create(
        name=name,
        tag=normalized_tag,
        description=normalized_description,
    )


def get_workspace_by_name(name: str) -> Workspace:
    try:
        return Workspace.objects.get(name=name)
    except Workspace.DoesNotExist as exc:
        raise WorkspaceNotFoundError(f"Workspace not found: {name}") from exc


def serialize_workspace_for_api(workspace: Workspace) -> dict:
    return {
        "name": workspace.name,
        "tag": opt.optional_field_for_api(workspace.tag),
        "description": opt.optional_field_for_api(workspace.description),
        "created_at": workspace.created_at,
    }


def move_workspace_media_dir(old_name: str, new_name: str) -> None:
    if old_name == new_name:
        return
    old_path = os.path.join(settings.MEDIA_ROOT, "workspaces", old_name)
    new_path = os.path.join(settings.MEDIA_ROOT, "workspaces", new_name)
    if os.path.exists(new_path):
        if os.path.isdir(new_path) and not os.listdir(new_path):
            os.rmdir(new_path)
        else:
            raise WorkspaceValidationError(
                f"Cannot rename workspace: media path already exists for '{new_name}'"
            )
    if os.path.exists(old_path):
        os.rename(old_path, new_path)
    else:
        os.makedirs(new_path, exist_ok=True)


def update_workspace(current_name: str, updates: dict) -> Workspace:
    allowed = {"name", "tag", "description"}
    unknown = set(updates) - allowed
    if unknown:
        raise WorkspaceValidationError(
            f"Unknown fields: {', '.join(sorted(unknown))}"
        )
    if not updates:
        raise WorkspaceValidationError("No fields to update")

    workspace = get_workspace_by_name(current_name)
    if is_internal_chat_workspace_name(workspace.name):
        raise WorkspaceValidationError("Internal chat workspaces cannot be updated")

    old_name = workspace.name
    new_name = old_name
    update_fields: list[str] = []

    if "name" in updates:
        candidate = updates["name"]
        if not candidate or not str(candidate).strip():
            raise WorkspaceValidationError("Workspace name required")
        new_name = str(candidate).strip()
        if is_reserved_workspace_name(new_name):
            raise WorkspaceValidationError(
                f"Workspace name '{new_name}' is reserved"
            )
        if (
            new_name != old_name
            and Workspace.objects.filter(name=new_name).exists()
        ):
            raise WorkspaceValidationError(
                f"Workspace already exists: {new_name}"
            )
        workspace.name = new_name
        update_fields.append("name")

    if "tag" in updates:
        try:
            workspace.tag = opt.normalize_tag(updates["tag"])
        except ValueError as exc:
            raise WorkspaceValidationError(str(exc)) from exc
        update_fields.append("tag")

    if "description" in updates:
        try:
            workspace.description = opt.normalize_description(updates["description"])
        except ValueError as exc:
            raise WorkspaceValidationError(str(exc)) from exc
        update_fields.append("description")

    if new_name != old_name:
        move_workspace_media_dir(old_name, new_name)

    try:
        with transaction.atomic():
            workspace.save(update_fields=update_fields)
    except Exception:
        if new_name != old_name:
            if os.path.exists(
                os.path.join(settings.MEDIA_ROOT, "workspaces", new_name)
            ):
                shutil.move(
                    os.path.join(settings.MEDIA_ROOT, "workspaces", new_name),
                    os.path.join(settings.MEDIA_ROOT, "workspaces", old_name),
                )
        raise

    if new_name != old_name:
        rename_workspace_vectors(workspace.pk, new_name)

    return workspace


def require_default_upload_workspace() -> Workspace:
    workspace = get_default_upload_workspace()
    if workspace is None:
        raise Workspace.DoesNotExist(
            "No workspace exists; create a workspace or pass workspace_name"
        )
    return workspace
