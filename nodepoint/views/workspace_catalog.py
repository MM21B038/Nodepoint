from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.enums import GroupTag
from nodepoint.services import workspace_catalog
from nodepoint.services.owner_scope import OwnerNotAccessibleError, OwnerScopeError, parse_owner_id_from_request
from nodepoint.services.workspace_group import (
    AmbiguousGroupError,
    GroupNotFoundError,
    get_group_by_name,
)


class WorkspaceStatsAPIView(AuthenticatedAPIView):
    """GET /api/workspace/stats/ — total, in_group, and ungrouped workspace counts."""

    def get(self, request):
        return Response(workspace_catalog.get_workspace_count_stats(actor=request.user))


class WorkspacePageAPIView(AuthenticatedAPIView):
    """
    GET /api/workspace/page/ — paginated workspaces with file/chunk/entity/relation counts.

    Query: page, page_size, group (optional — filter to members of that group)
    """

    def get(self, request):
        try:
            group_name = workspace_catalog.parse_group_filter(
                request.query_params.get("group")
            )
            group_owner_id = parse_owner_id_from_request(request)
            if group_name:
                group = get_group_by_name(
                    group_name,
                    actor=request.user,
                    owner_id=group_owner_id,
                )
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
        except AmbiguousGroupError as exc:
            return Response(
                {"error": str(exc), "candidates": exc.candidates},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except (OwnerScopeError, OwnerNotAccessibleError) as exc:
            code = (
                status.HTTP_403_FORBIDDEN
                if isinstance(exc, OwnerNotAccessibleError)
                else status.HTTP_400_BAD_REQUEST
            )
            return Response({"error": str(exc)}, status=code)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = workspace_catalog.list_workspaces_paginated(
            actor=request.user,
            group_name=group_name,
            group_owner_id=group_owner_id,
            page=page,
            page_size=page_size,
            include_counts=include_counts,
        )
        return Response(payload)
