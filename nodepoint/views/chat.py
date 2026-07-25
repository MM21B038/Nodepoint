from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView

from nodepoint.views.chat_sessions import SESSION_REQUIRED_LEGACY


class GroupChatAPIView(AuthenticatedAPIView):
    """Legacy GET/DELETE — requires explicit session_id via sessions API."""

    def get(self, request, name):
        return Response(
            {
                **SESSION_REQUIRED_LEGACY,
                "hint": SESSION_REQUIRED_LEGACY["hint"].replace(
                    "<workspace>", f"group/{name}"
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def delete(self, request, name):
        return Response(
            {
                **SESSION_REQUIRED_LEGACY,
                "hint": "Use DELETE /api/chat/group/"
                f"{name}/sessions/<session_id>/ to delete a session.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class WorkspaceChatAPIView(AuthenticatedAPIView):
    """Legacy GET/DELETE — requires explicit session_id via sessions API."""

    def get(self, request, workspace_name: str):
        return Response(SESSION_REQUIRED_LEGACY, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, workspace_name: str):
        return Response(
            {
                **SESSION_REQUIRED_LEGACY,
                "hint": "Use DELETE /api/chat/"
                f"{workspace_name}/sessions/<session_id>/ to delete a session.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
