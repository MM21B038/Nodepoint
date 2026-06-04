"""Shared request → workspace/group resolution with owner disambiguation."""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from nodepoint.services.owner_scope import OwnerNotAccessibleError, OwnerScopeError, parse_owner_id_from_request
from nodepoint.services.workspace import (
    AmbiguousWorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceValidationError,
    get_workspace_by_name,
)
from nodepoint.services.workspace_group import (
    AmbiguousGroupError,
    GroupNotFoundError,
    GroupError,
    get_group_by_name,
)


def _owner_scope_response(exc: OwnerScopeError | OwnerNotAccessibleError) -> Response:
    code = (
        status.HTTP_403_FORBIDDEN
        if isinstance(exc, OwnerNotAccessibleError)
        else status.HTTP_400_BAD_REQUEST
    )
    return Response({"error": str(exc)}, status=code)


def resolve_workspace_response(request, name: str):
    try:
        owner_id = parse_owner_id_from_request(request)
    except (OwnerScopeError, OwnerNotAccessibleError) as exc:
        return None, _owner_scope_response(exc)
    try:
        workspace = get_workspace_by_name(
            name.strip(), actor=request.user, owner_id=owner_id
        )
    except AmbiguousWorkspaceError as exc:
        return None, Response(
            {"error": str(exc), "candidates": exc.candidates},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except WorkspaceNotFoundError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return workspace, None


def resolve_group_response(request, name: str):
    try:
        owner_id = parse_owner_id_from_request(request)
    except (OwnerScopeError, OwnerNotAccessibleError) as exc:
        return None, _owner_scope_response(exc)
    try:
        group = get_group_by_name(name.strip(), actor=request.user, owner_id=owner_id)
    except AmbiguousGroupError as exc:
        return None, Response(
            {"error": str(exc), "candidates": exc.candidates},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except GroupNotFoundError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return group, None
