import os

import django_rq
from django.conf import settings
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.models import Workspace, Document
from nodepoint.services.document import doc_preprocess
from nodepoint.services.workspace import get_default_flagged_workspace

ALLOWED_EXTENSIONS = {".txt", ".md", ".text"}


def _allowed_filename(name: str) -> bool:
    return os.path.splitext(name.lower())[1] in ALLOWED_EXTENSIONS


class UploadDocumentAPIView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        workspace_name = request.data.get("workspace_name")
        uploaded_file = request.FILES.get("file")

        if not uploaded_file:
            return Response({"error": "No file uploaded"}, status=400)

        if not _allowed_filename(uploaded_file.name):
            return Response(
                {
                    "error": "Unsupported file type. Allowed: .txt, .md, .text",
                },
                status=400,
            )

        if workspace_name:
            try:
                workspace = Workspace.objects.get(name=workspace_name)
            except Workspace.DoesNotExist:
                return Response({"error": "Workspace not found"}, status=404)
        else:
            workspace = get_default_flagged_workspace()
            if workspace is None:
                return Response(
                    {
                        "error": (
                            "No starred workspace; create a workspace and "
                            "set is_flag=true (toggle-flag), or pass workspace_name"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        document = Document.objects.create(
            workspace=workspace,
            file_name=uploaded_file.name,
            file=uploaded_file,
        )

        queue = django_rq.get_queue("default")
        queue.enqueue(doc_preprocess, document_ids=[document.id])

        return Response(
            {
                "message": "File uploaded successfully",
                "id": str(document.id),
                "file_name": document.file_name,
                "file_path": document.file.path,
                "file_url": document.file.url,
                "status": document.status,
            }
        )


class ListWorkspaceDocumentsAPIView(APIView):
    def get(self, request, workspace_name):
        try:
            workspace = Workspace.objects.get(name=workspace_name)
        except Workspace.DoesNotExist:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        files = []
        for doc in workspace.documents.all().order_by("-created_at"):
            files.append(
                {
                    "id": str(doc.id),
                    "file_name": doc.file_name,
                    "file_url": doc.file.url if doc.file else None,
                    "status": doc.status,
                    "content": doc.content,
                    "uploaded_at": doc.created_at,
                }
            )

        return Response(
            {
                "workspace": workspace.name,
                "total_files": len(files),
                "files": files,
            }
        )


class DeleteDocumentAPIView(APIView):
    def delete(self, request, workspace_name, file_name):
        try:
            document = Document.objects.get(
                workspace__name=workspace_name,
                file_name=file_name,
            )
        except Document.DoesNotExist:
            return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

        file_path = document.file.path if document.file else None
        document.delete()

        if file_path and os.path.exists(file_path):
            os.remove(file_path)

        return Response({"message": "Document deleted successfully"})
