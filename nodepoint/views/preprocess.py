from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services.preprocess_pipeline import enqueue_priority_workspace_preprocess
from nodepoint.services.preprocess_status import build_workspace_preprocess_status
from nodepoint.services.queue_status import (
    build_queue_status,
    build_workspaces_preprocess_summary,
)


def _parse_bool_param(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class QueueStatusAPIView(APIView):
    """GET /api/preprocess/queue-status/ — global RQ / Redis / DB preprocess snapshot."""

    def get(self, request):
        workspace = (request.query_params.get("workspace") or "").strip() or None
        return Response(build_queue_status(workspace=workspace))


class WorkspacesPreprocessSummaryAPIView(APIView):
    """GET /api/preprocess/workspaces-summary/ — not-ready workspaces in one call."""

    def get(self, request):
        return Response(build_workspaces_preprocess_summary())


class PreprocessStatusAPIView(APIView):
    def get(self, request, workspace_name=None):
        name = (workspace_name or "").strip()
        if not name:
            return Response(
                {"error": "Workspace name required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            workspace = Workspace.objects.get(name=name)
        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(build_workspace_preprocess_status(workspace))


class PreprocessWorkspaceAPIView(APIView):
    def post(self, request, workspace_name=None):
        name = (workspace_name or request.data.get("name", "")).strip()
        if not name:
            return Response(
                {"error": "Workspace name required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            workspace = Workspace.objects.get(name=name)
        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        params = {**request.query_params.dict(), **request.data}
        priority = _parse_bool_param(params.get("priority"), default=False)
        include_other_workspaces = _parse_bool_param(
            params.get("include_other_workspaces"),
            default=False,
        )

        result = enqueue_priority_workspace_preprocess(
            workspace.name,
            priority=priority,
            include_other_workspaces=include_other_workspaces,
        )

        return Response(
            {
                "message": result["message"],
                "priority_workspace": result["priority_workspace"],
                "priority_pipeline": result["priority_pipeline"],
                "other_workspaces": result["other_workspaces"],
            }
        )
