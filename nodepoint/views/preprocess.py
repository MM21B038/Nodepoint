import django_rq
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services.document import doc_preprocess


class PreprocessWorkspaceAPIView(APIView):
    def post(self, request, workspace_name=None):
        name = (workspace_name or request.data.get("name", "")).strip()
        if not name:
            return Response(
                {"error": "Workspace name required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            workspace = Workspace.objects.get(name=name)
        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        queue = django_rq.get_queue("default")
        queue.enqueue(doc_preprocess, workspace_name=workspace.name)

        return Response(
            {"message": f"Preprocessing queued for workspace '{workspace.name}'"}
        )
