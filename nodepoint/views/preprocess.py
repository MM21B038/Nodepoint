from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.services import workspace as workspace_svc
from nodepoint.services.preprocess_pipeline import enqueue_priority_workspace_preprocess
from nodepoint.services.preprocess_status import build_workspace_preprocess_status
from nodepoint.services.queue_status import (
    build_queue_status,
    build_workspaces_preprocess_summary,
)
from nodepoint.views.resource_lookup import resolve_workspace_response


def _parse_bool_param(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class QueueStatusAPIView(AuthenticatedAPIView):
    """GET /api/preprocess/queue-status/ — RQ/Redis/DB snapshot for visible workspaces."""

    def get(self, request):
        workspace_name = (request.query_params.get("workspace") or "").strip() or None
        workspace_id = None
        if workspace_name:
            ws, err = resolve_workspace_response(request, workspace_name)
            if err is not None:
                return err
            workspace_id = ws.pk
            workspace_name = ws.name
        try:
            payload = build_queue_status(
                workspace=workspace_name,
                workspace_id=workspace_id,
                actor=request.user,
            )
        except PermissionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(payload)


class WorkspacesPreprocessSummaryAPIView(AuthenticatedAPIView):
    """GET /api/preprocess/workspaces-summary/ — not-ready workspaces for this user."""

    def get(self, request):
        return Response(build_workspaces_preprocess_summary(actor=request.user))


class PreprocessStatusAPIView(AuthenticatedAPIView):
    def get(self, request, workspace_name=None):
        name = (workspace_name or "").strip()
        if not name:
            return Response(
                {"error": "Workspace name required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace, err = resolve_workspace_response(request, name)
        if err is not None:
            return err

        return Response(build_workspace_preprocess_status(workspace))


class PreprocessWorkspaceAPIView(AuthenticatedAPIView):
    def post(self, request, workspace_name=None):
        name = (workspace_name or request.data.get("name", "")).strip()
        if not name:
            return Response(
                {"error": "Workspace name required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace, err = resolve_workspace_response(request, name)
        if err is not None:
            return err

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
            actor=request.user,
        )

        return Response(
            {
                "message": result["message"],
                "priority_workspace": result["priority_workspace"],
                "priority_pipeline": result["priority_pipeline"],
                "other_workspaces": result["other_workspaces"],
            }
        )
