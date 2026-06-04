from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.services import kg_entity_search
from nodepoint.views.kg_scope import resolve_kg_scope, resolve_kg_scope_targets


class KnowledgeEntitySearchAPIView(AuthenticatedAPIView):
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

        workspace, group, err = resolve_kg_scope_targets(
            request, scope, actor=request.user
        )
        if err is not None:
            return err

        try:
            graph_filters, threshold, match_limit = (
                kg_entity_search.parse_entity_search_params(
                    depth_raw=request.query_params.get("depth"),
                    limit_raw=request.query_params.get("limit"),
                    entity_type_raw=request.query_params.get("entity_type"),
                    file_name_raw=request.query_params.get("file_name"),
                    threshold_raw=request.query_params.get("threshold"),
                    match_limit_raw=request.query_params.get("match_limit"),
                )
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if group is not None:
            payload = kg_entity_search.search_group_workspaces_by_name(
                group.name,
                query,
                graph_filters,
                threshold=threshold,
                match_limit=match_limit,
                actor=request.user,
                owner_id=group.owner_id,
            )
            return Response(payload)

        payload = kg_entity_search.search_workspace_by_name(
            workspace,
            query,
            graph_filters,
            threshold=threshold,
            match_limit=match_limit,
        )
        return Response(payload)
