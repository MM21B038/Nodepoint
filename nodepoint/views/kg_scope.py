from __future__ import annotations
from typing import Tuple

from dataclasses import dataclass

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from nodepoint.auth.users import User
from nodepoint.models import Workspace, WorkspaceGroup
from nodepoint.services import kg_graph
from nodepoint.services.owner_scope import (
    OwnerNotAccessibleError,
    OwnerScopeError,
    parse_owner_id_from_request,
)
from nodepoint.services.workspace import (
    AmbiguousWorkspaceError,
    WorkspaceNotFoundError,
    get_workspace_by_name,
)
from nodepoint.services.workspace_group import (
    AmbiguousGroupError,
    GroupNotFoundError,
    get_group_by_name,
)


@dataclass(frozen=True)
class KgScope:
    workspace_name: str | None
    group_name: str | None

    @property
    def is_group_scope(self) -> bool:
        return self.group_name is not None


def resolve_kg_scope(request: Request) -> Tuple[KgScope | None, str | None]:
    """
    Returns (scope, error_message). error_message is set for 400 responses.
    """
    workspace_name = (request.query_params.get("workspace_name") or "").strip() or None
    group_name = (request.query_params.get("group") or "").strip() or None

    scopes = sum(
        [
            bool(workspace_name),
            bool(group_name),
        ]
    )
    if scopes == 0:
        return None, "Provide workspace_name or group"
    if scopes > 1:
        return None, "Use either workspace_name or group, not both"

    return KgScope(workspace_name=workspace_name, group_name=group_name), None


def _owner_scope_response(exc: OwnerScopeError | OwnerNotAccessibleError) -> Response:
    code = (
        status.HTTP_403_FORBIDDEN
        if isinstance(exc, OwnerNotAccessibleError)
        else status.HTTP_400_BAD_REQUEST
    )
    return Response({"error": str(exc)}, status=code)


def resolve_kg_scope_targets(
    request: Request,
    scope: KgScope,
    *,
    actor: User,
) -> Tuple[Workspace | None, WorkspaceGroup | None, Response | None]:
    """
    Resolve workspace or group for shared-scope endpoints.
    Returns (workspace, group, error_response); exactly one of workspace/group is set on success.
    """
    try:
        owner_id = parse_owner_id_from_request(request)
    except (OwnerScopeError, OwnerNotAccessibleError) as exc:
        return None, None, _owner_scope_response(exc)

    if scope.is_group_scope:
        group_name = scope.group_name
        if group_name is None:
            return None, None, Response(
                {"error": "Provide group"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            group = get_group_by_name(
                group_name, actor=actor, owner_id=owner_id
            )
        except AmbiguousGroupError as exc:
            return None, None, Response(
                {"error": str(exc), "candidates": exc.candidates},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except GroupNotFoundError:
            return None, None, Response(
                {"error": f"Group not found: {group_name}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return None, group, None

    workspace_name = scope.workspace_name
    if workspace_name is None:
        return None, None, Response(
            {"error": "Provide workspace_name"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        workspace = get_workspace_by_name(
            workspace_name, actor=actor, owner_id=owner_id
        )
    except AmbiguousWorkspaceError as exc:
        return None, None, Response(
            {"error": str(exc), "candidates": exc.candidates},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except WorkspaceNotFoundError:
        return None, None, Response(
            {"error": "Workspace not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    return workspace, None, None


def parse_graph_filters_from_request(
    request: Request,
) -> Tuple[kg_graph.GraphFilters | None, str | None]:
    try:
        filters = kg_graph.parse_graph_filters(
            entity_type_raw=request.query_params.get("entity_type"),
            file_name_raw=request.query_params.get("file_name"),
            depth_raw=request.query_params.get("depth"),
            limit_raw=request.query_params.get("limit"),
        )
    except ValueError as exc:
        return None, str(exc)
    return filters, None
