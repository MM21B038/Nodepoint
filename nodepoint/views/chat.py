from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.services import chat_storage
from nodepoint.services.workspace import (
    get_or_create_flagged_chat_workspace,
    list_starred_workspace_names,
    resolve_workspace_for_chat,
)


def _flagged_query(request) -> bool:
    return request.query_params.get("flagged", "").lower() in ("1", "true", "yes")


def _serialize_flagged_chat_response(workspace, messages) -> dict:
    return {
        "flagged": True,
        "starred_workspaces": list_starred_workspace_names(),
        "messages": chat_storage.serialize_messages_for_api(messages),
    }


def _serialize_workspace_chat_response(workspace, messages) -> dict:
    return {
        "workspace": workspace.name,
        "is_flag": workspace.is_flag,
        "messages": chat_storage.serialize_messages_for_api(messages),
    }


class FlaggedChatAPIView(APIView):
    """GET/DELETE /api/chat/?flagged=true — separate chat across starred workspaces."""

    def get(self, request):
        if not _flagged_query(request):
            return Response(
                {"error": "Provide flagged=true"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace = get_or_create_flagged_chat_workspace()
        conversation, _root = chat_storage.get_or_create_workspace_chat(workspace)
        messages = chat_storage.load_root_messages(conversation.id)
        return Response(_serialize_flagged_chat_response(workspace, messages))

    def delete(self, request):
        if not _flagged_query(request):
            return Response(
                {"error": "Provide flagged=true"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace = get_or_create_flagged_chat_workspace()
        chat_storage.clear_workspace_chat(workspace)
        return Response(
            {
                "message": "Flagged-scope chat cleared",
                "flagged": True,
                "starred_workspaces": list_starred_workspace_names(),
            }
        )


class WorkspaceChatAPIView(APIView):
    def get(self, request, workspace_name: str):
        workspace = resolve_workspace_for_chat(workspace_name)
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=404)

        conversation, _root = chat_storage.get_or_create_workspace_chat(workspace)
        messages = chat_storage.load_root_messages(conversation.id)
        return Response(_serialize_workspace_chat_response(workspace, messages))

    def delete(self, request, workspace_name: str):
        workspace = resolve_workspace_for_chat(workspace_name)
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=404)

        chat_storage.clear_workspace_chat(workspace)
        return Response({"message": "Chat cleared", "workspace": workspace.name})
