from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView
from nodepoint.auth.users import request_actor

from nodepoint.services import kg_graph
from nodepoint.views.kg_scope import (
    parse_graph_filters_from_request,
    resolve_kg_scope,
    resolve_kg_scope_targets,
)


class KnowledgeGraphAPIView(AuthenticatedAPIView):
    def get(self, request):
        scope, error = resolve_kg_scope(request)
        if scope is None:
            return Response(
                {"error": error or "Provide workspace_name or group"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _workspace, group, err = resolve_kg_scope_targets(
            request, scope, actor=request_actor(request)
        )
        if err is not None:
            return err

        filters, filter_error = parse_graph_filters_from_request(request)
        if filters is None:
            return Response(
                {"error": filter_error or "Invalid graph filters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if group is not None:
            from nodepoint.services.group_scope import resolve_group_search_scope

            group_scope = resolve_group_search_scope(
                group.name, actor=request.user, owner_id=group.owner_id, group=group
            )
            graphs = kg_graph.build_filtered_graphs_for_group(
                group.name,
                filters,
                actor=request.user,
                owner_id=group.owner_id,
            )
            return Response(
                {
                    "group": group.name,
                    "tag": group_scope.tag,
                    "graphs": graphs,
                }
            )

        if _workspace is None:
            return Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )

        graph = kg_graph.build_filtered_workspace_graph(_workspace, filters)
        return Response(graph)
