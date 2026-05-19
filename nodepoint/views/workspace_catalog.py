from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.services import workspace_catalog


class WorkspaceStatsAPIView(APIView):
    """GET /api/workspace/stats/ — total, flagged, and non-flagged workspace counts."""

    def get(self, request):
        return Response(workspace_catalog.get_workspace_count_stats())


class WorkspacePageAPIView(APIView):
    """
    GET /api/workspace/page/ — paginated workspaces with file/chunk/entity/relation counts.

    Query: page, page_size, flag (all | flagged | non_flagged)
    """

    def get(self, request):
        try:
            flag_filter = workspace_catalog.parse_flag_filter(
                request.query_params.get("flag")
            )
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = workspace_catalog.list_workspaces_paginated(
            flag_filter=flag_filter,
            page=page,
            page_size=page_size,
        )
        return Response(payload)
