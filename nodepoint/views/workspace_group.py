from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace
from nodepoint.services import workspace_group as group_svc


class CreateGroupAPIView(APIView):
    def post(self, request):
        name = request.data.get("name")
        try:
            group = group_svc.create_group(name)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Group created successfully",
                "group": {
                    "name": group.name,
                    "workspace_count": 0,
                    "created_at": group.created_at,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class ListGroupsAPIView(APIView):
    def get(self, request):
        return Response({"groups": group_svc.list_groups()})


class GroupDetailAPIView(APIView):
    def get(self, request, name):
        from nodepoint.services import workspace_catalog

        try:
            page, page_size = workspace_catalog.parse_pagination(
                request.query_params.get("page"),
                request.query_params.get("page_size"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(
                group_svc.get_group_detail(name, page=page, page_size=page_size)
            )
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, name):
        try:
            group_svc.delete_group(name)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response({"message": "Group deleted successfully", "group": name})


class AddWorkspaceToGroupAPIView(APIView):
    def post(self, request, name):
        workspace_name = request.data.get("workspace_name")
        if not workspace_name:
            return Response(
                {"error": "workspace_name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            workspace = Workspace.objects.get(name=workspace_name)
        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            group_svc.add_workspace_to_group(name, workspace)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Workspace added to group",
                "group": name,
                "workspace": workspace.name,
            }
        )


class RemoveWorkspaceFromGroupAPIView(APIView):
    def delete(self, request, name, workspace_name):
        try:
            workspace = Workspace.objects.get(name=workspace_name)
        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            group_svc.remove_workspace_from_group(name, workspace)
        except group_svc.GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except group_svc.GroupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "message": "Workspace removed from group",
                "group": name,
                "workspace": workspace.name,
            }
        )
