from django.urls import path

from nodepoint.views.workspace import (
    CreateWorkspaceAPIView,
    ListWorkspaceAPIView,
    DeleteWorkspaceAPIView,
    WorkspaceFlagStatusAPIView,
    ToggleWorkspaceFlagAPIView
)

from nodepoint.views.document import (
    UploadDocumentAPIView,
    ListWorkspaceDocumentsAPIView,
    DeleteDocumentAPIView
)

from nodepoint.views.preprocess import (
    PreprocessStatusAPIView,
    PreprocessWorkspaceAPIView,
)

from nodepoint.views.chat import FlaggedChatAPIView, WorkspaceChatAPIView

from nodepoint.views.knowledge_graph import KnowledgeGraphAPIView
from nodepoint.views.chat_summary import ChatSummaryAPIView

urlpatterns = [

    path(
        "workspace/create/",
        CreateWorkspaceAPIView.as_view()
    ),

    path(
        "workspace/list/",
        ListWorkspaceAPIView.as_view()
    ),

    path(
        "workspace/delete/<str:name>/",
        DeleteWorkspaceAPIView.as_view()
    ),

    path(
        "workspace/<str:name>/flag-status/",
        WorkspaceFlagStatusAPIView.as_view()
    ),

    path(
        "workspace/<str:name>/toggle-flag/",
        ToggleWorkspaceFlagAPIView.as_view()
    ),

    path(
        "document/upload/",
        UploadDocumentAPIView.as_view()
    ),

    path(
        "document/<str:workspace_name>/",
        ListWorkspaceDocumentsAPIView.as_view()
    ),

    path(
        "document/delete/<str:workspace_name>/<str:file_name>/",
        DeleteDocumentAPIView.as_view(),
    ),

    path(
        "workspace/<str:workspace_name>/preprocess-status/",
        PreprocessStatusAPIView.as_view(),
    ),

    path(
        "workspace/preprocess/<str:workspace_name>/",
        PreprocessWorkspaceAPIView.as_view()
    ),

    path(
        "chat/summary/",
        ChatSummaryAPIView.as_view(),
    ),

    path(
        "chat/",
        FlaggedChatAPIView.as_view(),
    ),

    path(
        "chat/<str:workspace_name>/",
        WorkspaceChatAPIView.as_view(),
    ),

    path(
        "knowledge-graph/",
        KnowledgeGraphAPIView.as_view(),
    ),
]