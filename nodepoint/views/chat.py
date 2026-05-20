from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.services import chat_storage
from nodepoint.services.workspace_group import (
    GroupNotFoundError,
    get_group_by_name,
    get_or_create_group_chat_workspace,
    list_group_workspace_names,
)


def _serialize_group_chat_response(group_name: str, messages) -> dict:
    return {
        "group": group_name,
        "workspaces": list_group_workspace_names(group_name),
        "messages": chat_storage.serialize_messages_for_api(messages),
    }


def _serialize_workspace_chat_response(workspace, messages) -> dict:
    from nodepoint.models import WorkspaceGroupMembership

    groups = list(
        WorkspaceGroupMembership.objects.filter(workspace=workspace)
        .select_related("group")
        .order_by("group__name")
        .values_list("group__name", flat=True)
    )
    return {
        "workspace": workspace.name,
        "groups": groups,
        "messages": chat_storage.serialize_messages_for_api(messages),
    }


class GroupChatAPIView(APIView):
    """GET/DELETE /api/chat/group/<name>/ — chat scoped to a workspace group."""

    def get(self, request, name):
        try:
            get_group_by_name(name)
        except GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        workspace = get_or_create_group_chat_workspace(name)
        conversation, _root = chat_storage.get_or_create_workspace_chat(workspace)
        messages = chat_storage.load_root_messages(conversation.id)
        return Response(_serialize_group_chat_response(name, messages))

    def delete(self, request, name):
        try:
            get_group_by_name(name)
        except GroupNotFoundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        workspace = get_or_create_group_chat_workspace(name)
        chat_storage.clear_workspace_chat(workspace)
        return Response(
            {
                "message": "Group chat cleared",
                "group": name,
                "workspaces": list_group_workspace_names(name),
            }
        )


class WorkspaceChatAPIView(APIView):
    def get(self, request, workspace_name: str):
        from nodepoint.services.workspace import resolve_workspace_for_chat

        workspace = resolve_workspace_for_chat(workspace_name)
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=404)

        conversation, _root = chat_storage.get_or_create_workspace_chat(workspace)
        messages = chat_storage.load_root_messages(conversation.id)
        return Response(_serialize_workspace_chat_response(workspace, messages))

    def delete(self, request, workspace_name: str):
        from nodepoint.services.workspace import resolve_workspace_for_chat

        workspace = resolve_workspace_for_chat(workspace_name)
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=404)

        chat_storage.clear_workspace_chat(workspace)
        return Response({"message": "Chat cleared", "workspace": workspace.name})
