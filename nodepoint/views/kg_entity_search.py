from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services import kg_entity_search
from nodepoint.views.kg_scope import resolve_kg_scope


class KnowledgeEntitySearchAPIView(APIView):
    """
    Fuzzy entity name search with optional subgraph (depth / limit).

    GET /api/knowledge/entities/search/?q=alice&workspace_name=...
    """

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        if not query:
            return Response(
                {"error": "Provide query parameter q"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scope, error = resolve_kg_scope(request)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        try:
            graph_filters, threshold, match_limit = (
                kg_entity_search.parse_entity_search_params(
                    depth_raw=request.query_params.get("depth"),
                    limit_raw=request.query_params.get("limit"),
                    entity_type_raw=request.query_params.get("entity_type"),
                    threshold_raw=request.query_params.get("threshold"),
                    match_limit_raw=request.query_params.get("match_limit"),
                )
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if scope.flagged:
            payload = kg_entity_search.search_flagged_workspaces_by_name(
                query,
                graph_filters,
                threshold=threshold,
                match_limit=match_limit,
            )
            return Response(payload)

        try:
            payload = kg_entity_search.search_by_name_for_workspace_name(
                scope.workspace_name,
                query,
                graph_filters,
                threshold=threshold,
                match_limit=match_limit,
            )
        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(payload)
