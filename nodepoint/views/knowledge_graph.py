from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services import kg_graph
from nodepoint.views.kg_scope import parse_graph_filters_from_request, resolve_kg_scope


class KnowledgeGraphAPIView(APIView):
    def get(self, request):
        scope, error = resolve_kg_scope(request)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        filters, filter_error = parse_graph_filters_from_request(request)
        if filter_error:
            return Response({"error": filter_error}, status=status.HTTP_400_BAD_REQUEST)

        if scope.is_group_scope:
            graphs = kg_graph.build_filtered_graphs_for_group(
                scope.group_name,
                filters,
            )
            return Response({"group": scope.group_name, "graphs": graphs})

        try:
            graph = kg_graph.build_filtered_graph_for_workspace_name(
                scope.workspace_name,
                filters,
            )
        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(graph)
