import os
import shutil
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from nodepoint.models import Workspace
from nodepoint.services import optional_fields as opt
from nodepoint.services import workspace as workspace_svc


class CreateWorkspaceAPIView(APIView):

    def post(self, request):

        name = request.data.get("name")
        tag = request.data.get("tag")
        description = request.data.get("description")

        try:
            workspace = workspace_svc.create_workspace(
                name, tag=tag, description=description
            )
        except workspace_svc.WorkspaceValidationError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
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
                "tag": opt.optional_field_for_api(workspace.tag),
                "description": opt.optional_field_for_api(workspace.description),
                "created_at": workspace.created_at
            }
        })
    
class ListWorkspaceAPIView(APIView):
    """
    GET /api/workspace/list/ — lightweight paginated workspace list (no KG counts).

    Query: page, page_size (same defaults as /api/workspace/page/).
    For file/entity counts use GET /api/workspace/page/.
    """

    def get(self, request):
        from nodepoint.services import workspace_catalog

        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = workspace_catalog.list_workspaces_paginated(
            page=page,
            page_size=page_size,
            include_counts=False,
        )
        return Response(payload)


class UpdateWorkspaceAPIView(APIView):

    def patch(self, request, name):
        allowed = {"name", "tag", "description"}
        updates = {key: request.data[key] for key in allowed if key in request.data}
        if not updates:
            return Response(
                {"error": "No fields to update"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            workspace = workspace_svc.update_workspace(name, updates)
        except workspace_svc.WorkspaceNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except workspace_svc.WorkspaceValidationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        body = {
            "message": "Workspace updated successfully",
            "workspace": workspace_svc.serialize_workspace_for_api(workspace),
        }
        if workspace.name != name:
            body["previous_name"] = name
        return Response(body)

class DeleteWorkspaceAPIView(APIView):

    def delete(self, request, name):

        try:
            workspace = Workspace.objects.get(name=name)

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

        workspace.delete()

        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path)

        return Response({
            "message": "Workspace deleted successfully"
        })
