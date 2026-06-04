from __future__ import annotations

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from nodepoint.auth.permissions import ApiKeyScopePermission
from nodepoint.auth.visibility import AccessDenied, require_group_access, require_workspace_access
from nodepoint.models import Workspace, WorkspaceGroup


class AuthenticatedAPIView(APIView):
    permission_classes = [IsAuthenticated, ApiKeyScopePermission]


def check_workspace_access(user, workspace: Workspace) -> None:
    try:
        require_workspace_access(user, workspace)
    except AccessDenied as exc:
        raise PermissionDenied(str(exc)) from exc


def check_group_access(user, group: WorkspaceGroup) -> None:
    try:
        require_group_access(user, group)
    except AccessDenied as exc:
        raise PermissionDenied(str(exc)) from exc
