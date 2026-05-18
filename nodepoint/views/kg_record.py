from uuid import UUID

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nodepoint.services.kg_records import (
    RecordAccessError,
    RecordNotFoundError,
    get_chunk,
    get_document,
    get_entity,
    get_relation,
)


class KnowledgeEntityDetailAPIView(APIView):
    def get(self, request, record_id: UUID):
        workspace_name = (request.query_params.get("workspace_name") or "").strip() or None
        allowed = [workspace_name] if workspace_name else None
        try:
            data = get_entity(record_id, allowed_workspaces=allowed)
        except RecordNotFoundError:
            return Response({"error": "Entity not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class KnowledgeRelationDetailAPIView(APIView):
    def get(self, request, record_id: UUID):
        workspace_name = (request.query_params.get("workspace_name") or "").strip() or None
        allowed = [workspace_name] if workspace_name else None
        try:
            data = get_relation(record_id, allowed_workspaces=allowed)
        except RecordNotFoundError:
            return Response({"error": "Relation not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class KnowledgeChunkDetailAPIView(APIView):
    def get(self, request, record_id: UUID):
        workspace_name = (request.query_params.get("workspace_name") or "").strip() or None
        allowed = [workspace_name] if workspace_name else None
        try:
            data = get_chunk(record_id, allowed_workspaces=allowed)
        except RecordNotFoundError:
            return Response({"error": "Chunk not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class KnowledgeDocumentDetailAPIView(APIView):
    def get(self, request, record_id: UUID):
        workspace_name = (request.query_params.get("workspace_name") or "").strip() or None
        allowed = [workspace_name] if workspace_name else None
        try:
            data = get_document(record_id, allowed_workspaces=allowed)
        except RecordNotFoundError:
            return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)
