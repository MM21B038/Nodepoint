from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services import kg_graph


class KnowledgeGraphAPIView(APIView):
    def get(self, request):
        workspace_name = request.query_params.get("workspace_name")
        flagged = request.query_params.get("flagged", "").lower() in (
            "1",
            "true",
            "yes",
        )

        if flagged and workspace_name:
            return Response(
                {"error": "Use either workspace_name or flagged=true, not both"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not flagged and not workspace_name:
            return Response(
                {"error": "Provide workspace_name or flagged=true"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if flagged:
            return Response({"graphs": kg_graph.build_graphs_for_flagged_workspaces()})

        try:
            graph = kg_graph.build_graph_for_workspace_name(workspace_name)
        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(graph)
