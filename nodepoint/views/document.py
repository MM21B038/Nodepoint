import os

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.enums import Status
from nodepoint.models import Workspace, Document
from nodepoint.services.preprocess_pipeline import enqueue_preprocess_pipeline
from nodepoint.services.workspace import require_default_upload_workspace

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
                workspace = Workspace.objects.create(name=workspace_name)
        else:
            try:
                workspace = require_default_upload_workspace()
            except Workspace.DoesNotExist:
                return Response(
                    {
                        "error": (
                            "No workspace exists; create a workspace or pass "
                            "workspace_name"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        replaced = False
        try:
            with transaction.atomic():
                document = (
                    Document.objects.select_for_update()
                    .filter(
                        workspace=workspace,
                        file_name=uploaded_file.name,
                    )
                    .first()
                )
                if document is None:
                    document = Document.objects.create(
                        workspace=workspace,
                        file_name=uploaded_file.name,
                        file=uploaded_file,
                    )
                else:
                    document.file.save(uploaded_file.name, uploaded_file, save=False)
                    document.status = Status.PENDING
                    document.content = False
                    document.save(update_fields=["file", "status", "content"])
                    replaced = True
        except IntegrityError:
            document = Document.objects.get(
                workspace=workspace,
                file_name=uploaded_file.name,
            )
            document.file.save(uploaded_file.name, uploaded_file, save=False)
            document.status = Status.PENDING
            document.content = False
            document.save(update_fields=["file", "status", "content"])
            replaced = True

        pipeline = enqueue_preprocess_pipeline(
            uploaded_document_id=document.id,
            workspace_name=workspace.name,
        )

        return Response(
            {
                "message": "File uploaded successfully",
                "replaced": replaced,
                "pipeline": pipeline,
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
