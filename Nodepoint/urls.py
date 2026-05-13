from django.urls import path

from Nodepoint.views.workspace import (
    CreateWorkspaceAPIView,
    ListWorkspaceAPIView,
    DeleteWorkspaceAPIView,
    WorkspaceFlagStatusAPIView,
    ToggleWorkspaceFlagAPIView
)

from Nodepoint.views.document import (
    UploadDocumentAPIView,
    ListWorkspaceFilesAPIView,
    DeleteDocumentAPIView
)

from Nodepoint.views.preprocess import (
    PreprocessWorkspaceAPIView
)

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
        ListWorkspaceFilesAPIView.as_view()
    ),

    path(
        "document/delete/<str:file_name>/",
        DeleteDocumentAPIView.as_view()
    ),

    path(
        "workspace/preprocess/<str:workspace_name>/",
        PreprocessWorkspaceAPIView.as_view()
    )
]