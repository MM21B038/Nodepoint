import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from Nodepoint.models import Workspace, Document


class UploadDocumentAPIView(APIView):

    parser_classes = [MultiPartParser]

    def post(self, request):

        workspace_id = request.data.get("workspace_id")
        uploaded_file = request.FILES.get("file")

        if not uploaded_file:
            return Response(
                {"error": "No file uploaded"},
                status=400
            )

        try:
            workspace = Workspace.objects.get(
                id=workspace_id
            )

        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=404
            )

        document = Document.objects.create(
            workspace=workspace,
            file_name=uploaded_file.name,
            file=uploaded_file
        )

        return Response({
            "message": "File uploaded successfully",
            "file_name": document.file_name,
            "file_path": document.file.path,
            "file_url": document.file.url
        })
    
class ListWorkspaceFilesAPIView(APIView):

    def get(self, request, workspace_name):

        try:
            workspace = Workspace.objects.get(
                name=workspace_name
            )

        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        documents = workspace.documents.all()

        files = []

        for doc in documents:
            files.append({
                "uuid": str(doc.uuid),
                "file_name": doc.file_name,
                "file_url": doc.file.url if doc.file else None,
                "uploaded_at": doc.created_at
            })

        return Response({
            "workspace": workspace.name,
            "total_files": len(files),
            "files": files
        })
    
class DeleteDocumentAPIView(APIView):

    def delete(self, request, file_name):

        try:
            document = Document.objects.get(file_name=file_name)

        except Document.DoesNotExist:
            return Response(
                {"error": "Document not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Store file path before deleting model
        file_path = document.file.path if document.file else None

        # Delete database record
        document.delete()

        # Delete physical file from media folder
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

        return Response({
            "message": "Document deleted successfully"
        })