from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.services import chat_storage
from nodepoint.views.kg_scope import resolve_kg_scope, resolve_kg_scope_targets


class ChatSummaryAPIView(AuthenticatedAPIView):
    def get(self, request):
        scope, error = resolve_kg_scope(request)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        _workspace, group, err = resolve_kg_scope_targets(
            request, scope, actor=request.user
        )
        if err is not None:
            return err

        if group is not None:
            return Response(
                chat_storage.list_chat_summary_for_group(
                    group.name,
                    actor=request.user,
                    owner_id=group.owner_id,
                )
            )

        return Response(chat_storage.list_chat_summary_for_workspace(_workspace))
