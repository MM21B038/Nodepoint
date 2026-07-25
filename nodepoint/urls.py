from django.urls import path

from nodepoint.auth.views import (
    AccountDeleteAPIView,
    AccountDeletionStatusAPIView,
    AccountRecoverAPIView,
    ApiKeyDetailAPIView,
    ApiKeyListCreateAPIView,
    MeAllowedScopesAPIView,
    MeChangePasswordAPIView,
    MeAPIView,
    RegisterAPIView,
    ScopesListAPIView,
    TokenObtainAPIView,
    TokenRefreshAPIView,
    UsageMeAPIView,
    UsagePlatformAPIView,
    UsageUserAPIView,
    UserAllowedScopesAPIView,
    UserDetailAPIView,
    UserListCreateAPIView,
    UserPurgeAPIView,
)
from nodepoint.views.workspace import (
    CreateWorkspaceAPIView,
    DeleteWorkspaceAPIView,
    ListWorkspaceAPIView,
    UpdateWorkspaceAPIView,
    WorkspaceLookupAPIView,
)
from nodepoint.views.workspace_catalog import (
    WorkspacePageAPIView,
    WorkspaceStatsAPIView,
)
from nodepoint.views.document import (
    DeleteDocumentAPIView,
    ListWorkspaceDocumentsAPIView,
    UploadDocumentAPIView,
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
from nodepoint.views.chat_sessions import (
    GroupChatSessionClearAPIView,
    GroupChatSessionDetailAPIView,
    GroupChatSessionsAPIView,
    WorkspaceChatSessionClearAPIView,
    WorkspaceChatSessionDetailAPIView,
    WorkspaceChatSessionsAPIView,
)
from nodepoint.views.workspace_group import (
    AddFileToGroupAPIView,
    AddWorkspaceToGroupAPIView,
    CreateGroupAPIView,
    GroupAddOptionsAPIView,
    GroupDetailAPIView,
    GroupLookupAPIView,
    GroupMembersAPIView,
    ListGroupsAPIView,
    RemoveFileFromGroupAPIView,
    RemoveWorkspaceFromGroupAPIView,
    WorkspaceGroupOptionsAPIView,
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
    path("auth/register/", RegisterAPIView.as_view(), name="auth-register"),
    path("auth/token/", TokenObtainAPIView.as_view(), name="auth-token"),
    path("auth/token/refresh/", TokenRefreshAPIView.as_view(), name="auth-token-refresh"),
    path("auth/me/", MeAPIView.as_view(), name="auth-me"),
    path("auth/me/password/", MeChangePasswordAPIView.as_view(), name="auth-me-password"),
    path("auth/me/allowed-scopes/", MeAllowedScopesAPIView.as_view(), name="auth-me-allowed-scopes"),
    path("auth/account/delete/", AccountDeleteAPIView.as_view(), name="auth-account-delete"),
    path("auth/account/recover/", AccountRecoverAPIView.as_view(), name="auth-account-recover"),
    path(
        "auth/account/deletion-status/",
        AccountDeletionStatusAPIView.as_view(),
        name="auth-account-deletion-status",
    ),
    path("auth/users/", UserListCreateAPIView.as_view(), name="auth-users"),
    path("auth/users/<int:user_id>/", UserDetailAPIView.as_view(), name="auth-user-detail"),
    path(
        "auth/users/<int:user_id>/purge/",
        UserPurgeAPIView.as_view(),
        name="auth-user-purge",
    ),
    path(
        "auth/users/<int:user_id>/allowed-scopes/",
        UserAllowedScopesAPIView.as_view(),
        name="auth-user-allowed-scopes",
    ),
    path("auth/usage/", UsagePlatformAPIView.as_view(), name="auth-usage-platform"),
    path("auth/usage/me/", UsageMeAPIView.as_view(), name="auth-usage-me"),
    path("auth/usage/users/<int:user_id>/", UsageUserAPIView.as_view(), name="auth-usage-user"),
    path("auth/api-keys/", ApiKeyListCreateAPIView.as_view(), name="auth-api-keys"),
    path(
        "auth/api-keys/<uuid:key_id>/",
        ApiKeyDetailAPIView.as_view(),
        name="auth-api-key-detail",
    ),
    path("auth/scopes/", ScopesListAPIView.as_view(), name="auth-scopes"),
    path("workspace/create/", CreateWorkspaceAPIView.as_view(), name="workspace-create"),
    path("workspace/list/", ListWorkspaceAPIView.as_view(), name="workspace-list"),
    path("workspace/lookup/", WorkspaceLookupAPIView.as_view(), name="workspace-lookup"),
    path("workspace/stats/", WorkspaceStatsAPIView.as_view(), name="workspace-stats"),
    path("workspace/page/", WorkspacePageAPIView.as_view(), name="workspace-page"),
    path("workspace/update/<str:name>/", UpdateWorkspaceAPIView.as_view(), name="workspace-update"),
    path("workspace/delete/<str:name>/", DeleteWorkspaceAPIView.as_view(), name="workspace-delete"),
    path(
        "workspace/<str:workspace_name>/group-options/",
        WorkspaceGroupOptionsAPIView.as_view(),
        name="workspace-group-options",
    ),
    path("document/upload/", UploadDocumentAPIView.as_view(), name="document-upload"),
    path(
        "document/<str:workspace_name>/",
        ListWorkspaceDocumentsAPIView.as_view(),
        name="document-list",
    ),
    path(
        "document/delete/<str:workspace_name>/<str:file_name>/",
        DeleteDocumentAPIView.as_view(),
        name="document-delete",
    ),
    path(
        "preprocess/queue-status/",
        QueueStatusAPIView.as_view(),
        name="preprocess-queue-status",
    ),
    path(
        "preprocess/workspaces-summary/",
        WorkspacesPreprocessSummaryAPIView.as_view(),
        name="preprocess-workspaces-summary",
    ),
    path(
        "workspace/<str:workspace_name>/preprocess-status/",
        PreprocessStatusAPIView.as_view(),
        name="workspace-preprocess-status",
    ),
    path(
        "workspace/preprocess/<str:workspace_name>/",
        PreprocessWorkspaceAPIView.as_view(),
        name="workspace-preprocess",
    ),
    path("chat/summary/", ChatSummaryAPIView.as_view(), name="chat-summary"),
    path("group/create/", CreateGroupAPIView.as_view(), name="group-create"),
    path("group/list/", ListGroupsAPIView.as_view(), name="group-list"),
    path("group/lookup/", GroupLookupAPIView.as_view(), name="group-lookup"),
    path("group/<str:name>/members/", GroupMembersAPIView.as_view(), name="group-members"),
    path(
        "group/<str:name>/add-options/",
        GroupAddOptionsAPIView.as_view(),
        name="group-add-options",
    ),
    path("group/<str:name>/", GroupDetailAPIView.as_view(), name="group-detail"),
    path(
        "group/<str:name>/workspaces/",
        AddWorkspaceToGroupAPIView.as_view(),
        name="group-add-workspace",
    ),
    path(
        "group/<str:name>/workspaces/<str:workspace_name>/",
        RemoveWorkspaceFromGroupAPIView.as_view(),
        name="group-remove-workspace",
    ),
    path("group/<str:name>/files/", AddFileToGroupAPIView.as_view(), name="group-add-file"),
    path(
        "group/<str:name>/files/<uuid:document_id>/",
        RemoveFileFromGroupAPIView.as_view(),
        name="group-remove-file",
    ),
    path(
        "chat/group/<str:name>/sessions/<uuid:session_id>/clear/",
        GroupChatSessionClearAPIView.as_view(),
        name="group-chat-session-clear",
    ),
    path(
        "chat/group/<str:name>/sessions/<uuid:session_id>/",
        GroupChatSessionDetailAPIView.as_view(),
        name="group-chat-session-detail",
    ),
    path(
        "chat/group/<str:name>/sessions/",
        GroupChatSessionsAPIView.as_view(),
        name="group-chat-sessions",
    ),
    path("chat/group/<str:name>/", GroupChatAPIView.as_view(), name="group-chat-legacy"),
    path(
        "chat/<str:workspace_name>/sessions/<uuid:session_id>/clear/",
        WorkspaceChatSessionClearAPIView.as_view(),
        name="workspace-chat-session-clear",
    ),
    path(
        "chat/<str:workspace_name>/sessions/<uuid:session_id>/",
        WorkspaceChatSessionDetailAPIView.as_view(),
        name="workspace-chat-session-detail",
    ),
    path(
        "chat/<str:workspace_name>/sessions/",
        WorkspaceChatSessionsAPIView.as_view(),
        name="workspace-chat-sessions",
    ),
    path(
        "chat/<str:workspace_name>/",
        WorkspaceChatAPIView.as_view(),
        name="workspace-chat-legacy",
    ),
    path(
        "knowledge/entities/search/",
        KnowledgeEntitySearchAPIView.as_view(),
        name="knowledge-entity-search",
    ),
    path(
        "knowledge-graph/entity-types/",
        KnowledgeGraphEntityTypesAPIView.as_view(),
        name="knowledge-graph-entity-types",
    ),
    path("knowledge-graph/", KnowledgeGraphAPIView.as_view(), name="knowledge-graph"),
    path(
        "knowledge/entity/<uuid:record_id>/",
        KnowledgeEntityDetailAPIView.as_view(),
        name="knowledge-entity-detail",
    ),
    path(
        "knowledge/relation/<uuid:record_id>/",
        KnowledgeRelationDetailAPIView.as_view(),
        name="knowledge-relation-detail",
    ),
    path(
        "knowledge/chunk/<uuid:record_id>/",
        KnowledgeChunkDetailAPIView.as_view(),
        name="knowledge-chunk-detail",
    ),
    path(
        "knowledge/document/<uuid:record_id>/",
        KnowledgeDocumentDetailAPIView.as_view(),
        name="knowledge-document-detail",
    ),
]
