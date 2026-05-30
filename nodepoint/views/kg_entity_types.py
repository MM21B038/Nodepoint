from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services import kg_graph
from nodepoint.views.kg_scope import resolve_kg_scope, validate_kg_group_exists


class KnowledgeGraphEntityTypesAPIView(APIView):
    def get(self, request):
        scope, error = resolve_kg_scope(request)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        group_error = validate_kg_group_exists(scope)
        if group_error:
            return Response({"error": group_error}, status=status.HTTP_404_NOT_FOUND)

        if scope.is_group_scope:
            return Response(kg_graph.list_entity_types_for_group(scope.group_name))

        try:
            payload = kg_graph.list_entity_types_for_workspace_name(
                scope.workspace_name
            )
        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(payload)
