from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.services import kg_graph
from nodepoint.views.kg_scope import (
    parse_graph_filters_from_request,
    resolve_kg_scope,
    resolve_kg_scope_targets,
)


class KnowledgeGraphAPIView(AuthenticatedAPIView):
    def get(self, request):
        scope, error = resolve_kg_scope(request)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        _workspace, group, err = resolve_kg_scope_targets(
            request, scope, actor=request.user
        )
        if err is not None:
            return err

        filters, filter_error = parse_graph_filters_from_request(request)
        if filter_error:
            return Response({"error": filter_error}, status=status.HTTP_400_BAD_REQUEST)

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

        graph = kg_graph.build_filtered_workspace_graph(_workspace, filters)
        return Response(graph)
