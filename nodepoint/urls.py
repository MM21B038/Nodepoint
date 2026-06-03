from django.urls import path

from nodepoint.views.workspace import (
    CreateWorkspaceAPIView,
    ListWorkspaceAPIView,
    UpdateWorkspaceAPIView,
    DeleteWorkspaceAPIView,
)
from nodepoint.views.workspace_catalog import (
    WorkspacePageAPIView,
    WorkspaceStatsAPIView,
)

from nodepoint.views.document import (
    UploadDocumentAPIView,
    ListWorkspaceDocumentsAPIView,
    DeleteDocumentAPIView
)

from nodepoint.views.preprocess import (
    PreprocessStatusAPIView,
    PreprocessWorkspaceAPIView,
    QueueStatusAPIView,
    WorkspacesPreprocessSummaryAPIView,
)

from nodepoint.views.chat import (
    GroupChatAPIView,
    WorkspaceChatAPIView,
)
from nodepoint.views.workspace_group import (
    AddEntityToGroupAPIView,
    AddFileToGroupAPIView,
    AddRelationToGroupAPIView,
    AddWorkspaceToGroupAPIView,
    CreateGroupAPIView,
    GroupDetailAPIView,
    GroupMembersAPIView,
    ListGroupsAPIView,
    RemoveEntityFromGroupAPIView,
    RemoveFileFromGroupAPIView,
    RemoveRelationFromGroupAPIView,
    RemoveWorkspaceFromGroupAPIView,
)

from nodepoint.views.kg_entity_search import KnowledgeEntitySearchAPIView
from nodepoint.views.kg_entity_types import KnowledgeGraphEntityTypesAPIView
from nodepoint.views.knowledge_graph import KnowledgeGraphAPIView
from nodepoint.views.kg_record import (
    KnowledgeChunkDetailAPIView,
    KnowledgeDocumentDetailAPIView,
    KnowledgeEntityDetailAPIView,
    KnowledgeRelationDetailAPIView,
)
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
        "workspace/stats/",
        WorkspaceStatsAPIView.as_view(),
    ),
    path(
        "workspace/page/",
        WorkspacePageAPIView.as_view(),
    ),

    path(
        "workspace/update/<str:name>/",
        UpdateWorkspaceAPIView.as_view(),
    ),

    path(
        "workspace/delete/<str:name>/",
        DeleteWorkspaceAPIView.as_view()
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
        "preprocess/queue-status/",
        QueueStatusAPIView.as_view(),
    ),

    path(
        "preprocess/workspaces-summary/",
        WorkspacesPreprocessSummaryAPIView.as_view(),
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
        "group/create/",
        CreateGroupAPIView.as_view(),
    ),
    path(
        "group/list/",
        ListGroupsAPIView.as_view(),
    ),
    path(
        "group/<str:name>/members/",
        GroupMembersAPIView.as_view(),
    ),
    path(
        "group/<str:name>/",
        GroupDetailAPIView.as_view(),
    ),
    path(
        "group/<str:name>/workspaces/",
        AddWorkspaceToGroupAPIView.as_view(),
    ),
    path(
        "group/<str:name>/workspaces/<str:workspace_name>/",
        RemoveWorkspaceFromGroupAPIView.as_view(),
    ),
    path(
        "group/<str:name>/files/",
        AddFileToGroupAPIView.as_view(),
    ),
    path(
        "group/<str:name>/files/<uuid:document_id>/",
        RemoveFileFromGroupAPIView.as_view(),
    ),
    path(
        "group/<str:name>/entities/",
        AddEntityToGroupAPIView.as_view(),
    ),
    path(
        "group/<str:name>/entities/<uuid:entity_id>/",
        RemoveEntityFromGroupAPIView.as_view(),
    ),
    path(
        "group/<str:name>/relations/",
        AddRelationToGroupAPIView.as_view(),
    ),
    path(
        "group/<str:name>/relations/<uuid:relation_id>/",
        RemoveRelationFromGroupAPIView.as_view(),
    ),

    path(
        "chat/group/<str:name>/",
        GroupChatAPIView.as_view(),
    ),

    path(
        "chat/<str:workspace_name>/",
        WorkspaceChatAPIView.as_view(),
    ),

    path(
        "knowledge/entities/search/",
        KnowledgeEntitySearchAPIView.as_view(),
    ),
    path(
        "knowledge-graph/entity-types/",
        KnowledgeGraphEntityTypesAPIView.as_view(),
    ),
    path(
        "knowledge-graph/",
        KnowledgeGraphAPIView.as_view(),
    ),

    path(
        "knowledge/entity/<uuid:record_id>/",
        KnowledgeEntityDetailAPIView.as_view(),
    ),
    path(
        "knowledge/relation/<uuid:record_id>/",
        KnowledgeRelationDetailAPIView.as_view(),
    ),
    path(
        "knowledge/chunk/<uuid:record_id>/",
        KnowledgeChunkDetailAPIView.as_view(),
    ),
    path(
        "knowledge/document/<uuid:record_id>/",
        KnowledgeDocumentDetailAPIView.as_view(),
    ),
]
