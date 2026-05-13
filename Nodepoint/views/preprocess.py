import os
import threading
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from Nodepoint.models import Workspace
from Nodepoint.services.preprocess import preprocess


class PreprocessWorkspaceAPIView(APIView):

    def post(self, request):

        workspace_name = request.data.get("name", "").strip()

        if not workspace_name:
            return Response(
                {"error": "Workspace name required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            workspace = Workspace.objects.get(
                name=workspace_name
            )

        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        workspace_path = os.path.join(
            settings.MEDIA_ROOT,
            "workspaces",
            workspace.name
        )

        # Background thread
        thread = threading.Thread(
            target=preprocess,
            args=(workspace.name, workspace_path),
            daemon=True
        )

        thread.start()

        return Response({
            "message": f"✅ Preprocessing started for '{workspace.name}'"
        })