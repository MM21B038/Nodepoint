from __future__ import annotations

import os
import shutil

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet

from django.contrib.auth import get_user_model

from nodepoint.models import Workspace

User = get_user_model()
from nodepoint.auth.visibility import visible_workspaces_qs
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


class AmbiguousWorkspaceError(WorkspaceValidationError):
    def __init__(self, name: str, candidates: list[dict]):
        self.name = name
        self.candidates = candidates
        super().__init__(
            f"Multiple workspaces named '{name}'; specify owner_id or owner_username"
        )


def resolve_workspace_for_chat(
    workspace_name: str,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> Workspace | None:
    """Named workspace chat only (not group scope)."""
    name = workspace_name.strip()
    if is_internal_chat_workspace_name(name):
        return None
    if actor is None:
        return Workspace.objects.filter(name=name).first()
    try:
        return get_workspace_by_name(name, actor=actor, owner_id=owner_id)
    except (WorkspaceNotFoundError, AmbiguousWorkspaceError):
        return None


def is_reserved_workspace_name(name: str) -> bool:
    cleaned = name.strip()
    if cleaned.startswith(GROUP_CHAT_PREFIX):
        return True
    return cleaned == LEGACY_FLAGGED_CHAT_WORKSPACE_NAME


def create_workspace(
    name: str,
    *,
    owner: User,
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
    if Workspace.objects.filter(owner=owner, name=name).exists():
        raise WorkspaceValidationError(f"Workspace already exists for this owner: {name}")
    return Workspace.objects.create(
        name=name,
        owner=owner,
        tag=normalized_tag,
        description=normalized_description,
    )


def workspace_storage_relpath(workspace: Workspace) -> str:
    return os.path.join("workspaces", str(workspace.owner_id), workspace.name)


def workspace_storage_abspath(workspace: Workspace) -> str:
    return os.path.join(settings.MEDIA_ROOT, workspace_storage_relpath(workspace))


def _workspace_owner_candidates(workspaces: list[Workspace]) -> list[dict]:
    return [
        {
            "owner_id": ws.owner_id,
            "owner_username": ws.owner.username,
            "workspace_id": ws.pk,
        }
        for ws in workspaces
    ]


def get_workspace_by_name(
    name: str,
    *,
    actor: User | None = None,
    owner_id: int | None = None,
) -> Workspace:
    qs = Workspace.objects.filter(name=name).select_related("owner")
    if actor is not None:
        qs = qs.filter(pk__in=workspaces_for_actor(actor).values_list("pk", flat=True))
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)

    matches = list(qs)
    if not matches:
        raise WorkspaceNotFoundError(f"Workspace not found: {name}")
    if len(matches) > 1:
        raise AmbiguousWorkspaceError(name, _workspace_owner_candidates(matches))
    workspace = matches[0]
    if actor is not None:
        from nodepoint.auth.visibility import can_access_workspace

        if not can_access_workspace(actor, workspace):
            raise WorkspaceNotFoundError(f"Workspace not found: {name}")
    return workspace


def lookup_workspaces_by_name(
    name: str,
    *,
    actor: User,
    owner_id: int | None = None,
) -> dict:
    """Return visible workspaces matching name with owner info (disambiguation picker)."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise WorkspaceValidationError("Workspace name is required")
    qs = workspaces_for_actor(actor).filter(name=cleaned).select_related("owner")
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)
    matches = list(qs.order_by("owner__username", "name"))
    if not matches:
        raise WorkspaceNotFoundError(f"Workspace not found: {cleaned}")
    rows = [serialize_workspace_for_api(ws) for ws in matches]
    return {
        "name": cleaned,
        "ambiguous": len(rows) > 1,
        "matches": rows,
    }


def workspaces_for_actor(actor: User) -> QuerySet[Workspace]:
    return visible_workspaces_qs(actor)


def serialize_workspace_for_api(workspace: Workspace) -> dict:
    owner = workspace.owner
    return {
        "id": workspace.pk,
        "name": workspace.name,
        "owner_id": workspace.owner_id,
        "owner_username": owner.username,
        "tag": opt.optional_field_for_api(workspace.tag),
        "description": opt.optional_field_for_api(workspace.description),
        "created_at": workspace.created_at,
    }


def move_workspace_media_dir(
    workspace: Workspace, *, from_name: str, to_name: str
) -> None:
    if from_name == to_name:
        return
    old_path = os.path.join(
        settings.MEDIA_ROOT,
        "workspaces",
        str(workspace.owner_id),
        from_name,
    )
    new_path = os.path.join(
        settings.MEDIA_ROOT,
        "workspaces",
        str(workspace.owner_id),
        to_name,
    )
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


def update_workspace_instance(workspace: Workspace, updates: dict, *, actor: User) -> Workspace:
    """Apply metadata updates to an already-resolved workspace (no name re-lookup)."""
    allowed = {"name", "tag", "description"}
    unknown = set(updates) - allowed
    if unknown:
        raise WorkspaceValidationError(
            f"Unknown fields: {', '.join(sorted(unknown))}"
        )
    if not updates:
        raise WorkspaceValidationError("No fields to update")

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
            and Workspace.objects.filter(owner=workspace.owner, name=new_name).exists()
        ):
            raise WorkspaceValidationError(
                f"Workspace already exists for this owner: {new_name}"
            )
        if new_name != old_name:
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
        move_workspace_media_dir(workspace, from_name=old_name, to_name=new_name)
        workspace.name = new_name

    try:
        with transaction.atomic():
            workspace.save(update_fields=update_fields)
    except Exception:
        if new_name != old_name:
            new_abs = os.path.join(
                settings.MEDIA_ROOT,
                "workspaces",
                str(workspace.owner_id),
                new_name,
            )
            old_abs = os.path.join(
                settings.MEDIA_ROOT,
                "workspaces",
                str(workspace.owner_id),
                old_name,
            )
            if os.path.exists(new_abs):
                shutil.move(new_abs, old_abs)
        raise

    if new_name != old_name:
        rename_workspace_vectors(workspace.pk, new_name)

    return workspace


def update_workspace(
    current_name: str,
    updates: dict,
    *,
    actor: User,
    owner_id: int | None = None,
) -> Workspace:
    workspace = get_workspace_by_name(
        current_name, actor=actor, owner_id=owner_id
    )
    return update_workspace_instance(workspace, updates, actor=actor)


def require_default_upload_workspace(actor: User) -> Workspace:
    workspace = get_default_upload_workspace(actor)
    if workspace is None:
        raise WorkspaceNotFoundError(
            "No workspace exists; create a workspace or pass workspace_name"
        )
    return workspace
