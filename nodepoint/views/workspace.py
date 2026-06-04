import os
import shutil

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

from nodepoint.auth.mixins import AuthenticatedAPIView, check_workspace_access
from nodepoint.services import optional_fields as opt
from nodepoint.services import workspace as workspace_svc
from nodepoint.services.owner_scope import (
    OwnerNotAccessibleError,
    OwnerScopeError,
    parse_owner_id_from_request,
)
from nodepoint.services.workspace import workspace_storage_abspath
from nodepoint.views.resource_lookup import resolve_workspace_response


class CreateWorkspaceAPIView(AuthenticatedAPIView):

    def post(self, request):
        name = request.data.get("name")
        tag = request.data.get("tag")
        description = request.data.get("description")

        try:
            workspace = workspace_svc.create_workspace(
                name,
                owner=request.user,
                tag=tag,
                description=description,
            )
        except workspace_svc.WorkspaceValidationError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        os.makedirs(workspace_storage_abspath(workspace), exist_ok=True)

        return Response({
            "message": "Workspace created successfully",
            "workspace": workspace_svc.serialize_workspace_for_api(workspace),
        })


class ListWorkspaceAPIView(AuthenticatedAPIView):
    def get(self, request):
        from nodepoint.services import workspace_catalog

        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            group_owner_id = parse_owner_id_from_request(request)
        except (OwnerScopeError, OwnerNotAccessibleError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = workspace_catalog.list_workspaces_paginated(
            actor=request.user,
            page=page,
            page_size=page_size,
            include_counts=False,
            group_owner_id=group_owner_id,
        )
        return Response(payload)


class WorkspaceLookupAPIView(AuthenticatedAPIView):
    """GET /api/workspace/lookup/?name= — which owner(s) have a workspace with this name."""

    def get(self, request):
        name = request.query_params.get("name")
        if not name or not str(name).strip():
            return Response(
                {"error": "name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            owner_id = parse_owner_id_from_request(request)
        except (OwnerScopeError, OwnerNotAccessibleError) as exc:
            return Response(
                {"error": str(exc)},
                status=(
                    status.HTTP_403_FORBIDDEN
                    if isinstance(exc, OwnerNotAccessibleError)
                    else status.HTTP_400_BAD_REQUEST
                ),
            )
        try:
            payload = workspace_svc.lookup_workspaces_by_name(
                name,
                actor=request.user,
                owner_id=owner_id,
            )
        except workspace_svc.WorkspaceNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except workspace_svc.WorkspaceValidationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class UpdateWorkspaceAPIView(AuthenticatedAPIView):

    def patch(self, request, name):
        allowed = {"name", "tag", "description"}
        updates = {key: request.data[key] for key in allowed if key in request.data}
        if not updates:
            return Response(
                {"error": "No fields to update"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace, err = resolve_workspace_response(request, name)
        if err is not None:
            return err
        try:
            workspace = workspace_svc.update_workspace_instance(
                workspace, updates, actor=request.user
            )
        except workspace_svc.WorkspaceValidationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        body = {
            "message": "Workspace updated successfully",
            "workspace": workspace_svc.serialize_workspace_for_api(workspace),
        }
        if workspace.name != name:
            body["previous_name"] = name
        return Response(body)


class DeleteWorkspaceAPIView(AuthenticatedAPIView):

    def delete(self, request, name):
        workspace, err = resolve_workspace_response(request, name)
        if err is not None:
            return err

        workspace_path = workspace_storage_abspath(workspace)
        workspace.delete()

        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path)

        return Response({"message": "Workspace deleted successfully"})
