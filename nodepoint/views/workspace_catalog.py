from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.enums import GroupTag
from nodepoint.services import workspace_catalog
from nodepoint.services.workspace_group import GroupNotFoundError, get_group_by_name


class WorkspaceStatsAPIView(APIView):
    """GET /api/workspace/stats/ — total, in_group, and ungrouped workspace counts."""

    def get(self, request):
        return Response(workspace_catalog.get_workspace_count_stats())


class WorkspacePageAPIView(APIView):
    """
    GET /api/workspace/page/ — paginated workspaces with file/chunk/entity/relation counts.

    Query: page, page_size, group (optional — filter to members of that group)
    """

    def get(self, request):
        try:
            group_name = workspace_catalog.parse_group_filter(
                request.query_params.get("group")
            )
            if group_name:
                group = get_group_by_name(group_name)
                if group.tag != GroupTag.WORKSPACE:
                    return Response(
                        {
                            "error": (
                                f"workspace/page group filter requires a workspace-tagged "
                                f"group; '{group_name}' is tag '{group.tag}'"
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
            include_counts = workspace_catalog.parse_include_counts(
                request.query_params.get("include_counts")
            )
        except GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = workspace_catalog.list_workspaces_paginated(
            group_name=group_name,
            page=page,
            page_size=page_size,
            include_counts=include_counts,
        )
        return Response(payload)
