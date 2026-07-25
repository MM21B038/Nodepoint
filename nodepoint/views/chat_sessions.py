from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.models import Workspace, WorkspaceGroup
from nodepoint.services import chat_storage
from nodepoint.services.chat_storage import SessionNotFoundError
from nodepoint.services.workspace import is_internal_chat_workspace_name
from nodepoint.services.workspace_group import (
    get_or_create_group_chat_workspace,
    list_group_workspace_names,
)
from nodepoint.views.resource_lookup import resolve_group_response, resolve_workspace_response


SESSION_REQUIRED_LEGACY = {
    "error": "session_id required",
    "hint": "Use GET/POST /api/chat/<workspace>/sessions/ to list or create sessions, "
    "then GET /api/chat/<workspace>/sessions/<session_id>/ for history.",
}


def _resolve_chat_workspace(
    request, workspace_name: str
) -> Tuple[Workspace | None, Response | None]:
    workspace, err = resolve_workspace_response(request, workspace_name)
    if workspace is None:
        return None, err or Response(
            {"error": "Workspace not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if is_internal_chat_workspace_name(workspace.name):
        return None, Response(
            {"error": "Workspace not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    return workspace, None


def _resolve_chat_group(
    request, name: str
) -> Tuple[WorkspaceGroup | None, Response | None]:
    return resolve_group_response(request, name)


def _serialize_session_detail(
    conversation,
    messages,
    *,
    workspace_name: str | None = None,
    group_name: str | None = None,
    group_owner_id: int | None = None,
    actor=None,
) -> Dict[str, Any]:
    payload = {
        "session_id": str(conversation.id),
        "title": conversation.title,
        "messages": chat_storage.serialize_messages_for_api(messages),
    }
    if workspace_name is not None:
        payload["workspace"] = workspace_name
    if group_name is not None:
        payload["group"] = group_name
        payload["workspaces"] = list_group_workspace_names(
            group_name,
            actor=actor,
            owner_id=group_owner_id,
        )
    return payload


class WorkspaceChatSessionsAPIView(AuthenticatedAPIView):
    """GET/POST /api/chat/<workspace_name>/sessions/"""

    def get(self, request, workspace_name: str):
        workspace, err = _resolve_chat_workspace(request, workspace_name)
        if workspace is None:
            return err or Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(
            {
                "workspace": workspace.name,
                "sessions": chat_storage.list_sessions(workspace),
            }
        )

    def post(self, request, workspace_name: str):
        workspace, err = _resolve_chat_workspace(request, workspace_name)
        if workspace is None:
            return err or Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )
        title = (request.data.get("title") or "").strip() if request.data else ""
        conversation, _root = chat_storage.create_session(workspace, title=title)
        return Response(
            {
                "workspace": workspace.name,
                "session_id": str(conversation.id),
                "title": conversation.title,
                "created_at": conversation.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


class WorkspaceChatSessionDetailAPIView(AuthenticatedAPIView):
    """GET/PATCH/DELETE /api/chat/<workspace_name>/sessions/<session_id>/"""

    def get(self, request, workspace_name: str, session_id: uuid.UUID):
        workspace, err = _resolve_chat_workspace(request, workspace_name)
        if workspace is None:
            return err or Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            conversation = chat_storage.get_session(workspace, session_id)
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        messages = chat_storage.load_root_messages(conversation.id)
        return Response(
            _serialize_session_detail(
                conversation, messages, workspace_name=workspace.name
            )
        )

    def patch(self, request, workspace_name: str, session_id: uuid.UUID):
        workspace, err = _resolve_chat_workspace(request, workspace_name)
        if workspace is None:
            return err or Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )
        title = (request.data.get("title") or "").strip() if request.data else ""
        if not title:
            return Response(
                {"error": "title is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            conversation = chat_storage.update_session_title(
                workspace, session_id, title=title
            )
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "workspace": workspace.name,
                "session_id": str(conversation.id),
                "title": conversation.title,
            }
        )

    def delete(self, request, workspace_name: str, session_id: uuid.UUID):
        workspace, err = _resolve_chat_workspace(request, workspace_name)
        if workspace is None:
            return err or Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            chat_storage.get_session(workspace, session_id)
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        chat_storage.delete_session(session_id)
        return Response(
            {
                "message": "Session deleted",
                "workspace": workspace.name,
                "session_id": str(session_id),
            }
        )


class WorkspaceChatSessionClearAPIView(AuthenticatedAPIView):
    """POST /api/chat/<workspace_name>/sessions/<session_id>/clear/"""

    def post(self, request, workspace_name: str, session_id: uuid.UUID):
        workspace, err = _resolve_chat_workspace(request, workspace_name)
        if workspace is None:
            return err or Response(
                {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            chat_storage.get_session(workspace, session_id)
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        conversation, _root = chat_storage.clear_session(session_id)
        return Response(
            {
                "message": "Session cleared",
                "workspace": workspace.name,
                "session_id": str(conversation.id),
            }
        )


class GroupChatSessionsAPIView(AuthenticatedAPIView):
    """GET/POST /api/chat/group/<name>/sessions/"""

    def get(self, request, name: str):
        group, err = _resolve_chat_group(request, name)
        if group is None:
            return err or Response(
                {"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND
            )
        workspace = get_or_create_group_chat_workspace(
            group.name, actor=request.user, owner_id=group.owner_id
        )
        return Response(
            {
                "group": group.name,
                "workspaces": list_group_workspace_names(
                    group.name, actor=request.user, owner_id=group.owner_id
                ),
                "sessions": chat_storage.list_sessions(workspace),
            }
        )

    def post(self, request, name: str):
        group, err = _resolve_chat_group(request, name)
        if group is None:
            return err or Response(
                {"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND
            )
        workspace = get_or_create_group_chat_workspace(
            group.name, actor=request.user, owner_id=group.owner_id
        )
        title = (request.data.get("title") or "").strip() if request.data else ""
        conversation, _root = chat_storage.create_session(workspace, title=title)
        return Response(
            {
                "group": group.name,
                "session_id": str(conversation.id),
                "title": conversation.title,
                "created_at": conversation.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


class GroupChatSessionDetailAPIView(AuthenticatedAPIView):
    """GET/PATCH/DELETE /api/chat/group/<name>/sessions/<session_id>/"""

    def get(self, request, name: str, session_id: uuid.UUID):
        group, err = _resolve_chat_group(request, name)
        if group is None:
            return err or Response(
                {"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND
            )
        workspace = get_or_create_group_chat_workspace(
            group.name, actor=request.user, owner_id=group.owner_id
        )
        try:
            conversation = chat_storage.get_session(workspace, session_id)
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        messages = chat_storage.load_root_messages(conversation.id)
        return Response(
            _serialize_session_detail(
                conversation,
                messages,
                group_name=group.name,
                group_owner_id=group.owner_id,
                actor=request.user,
            )
        )

    def patch(self, request, name: str, session_id: uuid.UUID):
        group, err = _resolve_chat_group(request, name)
        if group is None:
            return err or Response(
                {"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND
            )
        workspace = get_or_create_group_chat_workspace(
            group.name, actor=request.user, owner_id=group.owner_id
        )
        title = (request.data.get("title") or "").strip() if request.data else ""
        if not title:
            return Response(
                {"error": "title is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            conversation = chat_storage.update_session_title(
                workspace, session_id, title=title
            )
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "group": group.name,
                "session_id": str(conversation.id),
                "title": conversation.title,
            }
        )

    def delete(self, request, name: str, session_id: uuid.UUID):
        group, err = _resolve_chat_group(request, name)
        if group is None:
            return err or Response(
                {"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND
            )
        workspace = get_or_create_group_chat_workspace(
            group.name, actor=request.user, owner_id=group.owner_id
        )
        try:
            chat_storage.get_session(workspace, session_id)
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        chat_storage.delete_session(session_id)
        return Response(
            {
                "message": "Session deleted",
                "group": group.name,
                "session_id": str(session_id),
            }
        )


class GroupChatSessionClearAPIView(AuthenticatedAPIView):
    """POST /api/chat/group/<name>/sessions/<session_id>/clear/"""

    def post(self, request, name: str, session_id: uuid.UUID):
        group, err = _resolve_chat_group(request, name)
        if group is None:
            return err or Response(
                {"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND
            )
        workspace = get_or_create_group_chat_workspace(
            group.name, actor=request.user, owner_id=group.owner_id
        )
        try:
            chat_storage.get_session(workspace, session_id)
        except SessionNotFoundError:
            return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)
        conversation, _root = chat_storage.clear_session(session_id)
        return Response(
            {
                "message": "Session cleared",
                "group": group.name,
                "session_id": str(conversation.id),
            }
        )
