import os
import shutil
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from Nodepoint.models import Workspace


class CreateWorkspaceAPIView(APIView):

    def post(self, request):

        name = request.data.get("name")

        if not name:
            return Response(
                {"error": "Workspace name required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        workspace = Workspace.objects.create(
            name=name
        )

        workspace_path = os.path.join(
            settings.MEDIA_ROOT,
            "workspaces",
            workspace.name
        )

        os.makedirs(
            workspace_path,
            exist_ok=True
        )
        
        return Response({
            "message": "Workspace created successfully",
            "workspace": {
                "name": workspace.name,
                "created_at": workspace.created_at
            }
        })
    
class ListWorkspaceAPIView(APIView):

    def get(self, request):

        workspaces = Workspace.objects.all().order_by("-created_at")

        data = []

        for ws in workspaces:
            data.append({
                "name": ws.name,
                "is_flag": ws.is_flag,
                "created_at": ws.created_at
            })

        return Response(data)
    
class DeleteWorkspaceAPIView(APIView):

    def delete(self, request, name):

        try:
            workspace = Workspace.objects.get(name=name)

        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Workspace folder path
        workspace_path = os.path.join(
            settings.MEDIA_ROOT,
            "workspaces",
            workspace.name
        )

        # Delete database records
        workspace.delete()

        # Delete physical workspace folder
        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path)

        return Response({
            "message": "Workspace deleted successfully"
        })

class WorkspaceFlagStatusAPIView(APIView):

    def get(self, request, name):

        try:
            workspace = Workspace.objects.get(name=name)

        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            "workspace": workspace.name,
            "is_flag": workspace.is_flag
        })

class ToggleWorkspaceFlagAPIView(APIView):

    def patch(self, request, name):

        try:
            workspace = Workspace.objects.get(name=name)

        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Toggle flag
        workspace.is_flag = not workspace.is_flag
        workspace.save()

        return Response({
            "message": "Workspace flag updated successfully",
            "workspace": workspace.name,
            "is_flag": workspace.is_flag
        })