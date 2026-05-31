from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services import chat_storage
from nodepoint.views.kg_scope import resolve_kg_scope


class ChatSummaryAPIView(APIView):
    def get(self, request):
        scope, error = resolve_kg_scope(request)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        if scope.is_group_scope:
            return Response(chat_storage.list_chat_summary_for_group(scope.group_name))

        try:
            workspace = Workspace.objects.get(name=scope.workspace_name)
        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(chat_storage.list_chat_summary_for_workspace(workspace))
