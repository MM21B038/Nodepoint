from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView
from nodepoint.auth.users import request_actor

from nodepoint.services import kg_graph
from nodepoint.views.kg_scope import resolve_kg_scope, resolve_kg_scope_targets


class KnowledgeGraphEntityTypesAPIView(AuthenticatedAPIView):
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

        if group is not None:
            return Response(
                kg_graph.list_entity_types_for_group(
                    group.name,
                    actor=request.user,
                    owner_id=group.owner_id,
                )
            )

        if _workspace is None:
            return Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )

        return Response(kg_graph.list_entity_types_for_workspace(_workspace))
