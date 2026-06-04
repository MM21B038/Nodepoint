from uuid import UUID

from rest_framework import status
from rest_framework.response import Response
from nodepoint.auth.mixins import AuthenticatedAPIView
from nodepoint.auth.visibility import allowed_workspace_ids

from nodepoint.services.kg_records import (
    RecordAccessError,
    RecordNotFoundError,
    get_chunk,
    get_document,
    get_entity,
    get_relation,
)


class KnowledgeEntityDetailAPIView(AuthenticatedAPIView):
    def get(self, request, record_id: UUID):
        allowed = allowed_workspace_ids(request.user)
        try:
            data = get_entity(record_id, allowed_workspace_ids=allowed)
        except RecordNotFoundError:
            return Response({"error": "Entity not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class KnowledgeRelationDetailAPIView(AuthenticatedAPIView):
    def get(self, request, record_id: UUID):
        allowed = allowed_workspace_ids(request.user)
        try:
            data = get_relation(record_id, allowed_workspace_ids=allowed)
        except RecordNotFoundError:
            return Response({"error": "Relation not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class KnowledgeChunkDetailAPIView(AuthenticatedAPIView):
    def get(self, request, record_id: UUID):
        allowed = allowed_workspace_ids(request.user)
        try:
            data = get_chunk(record_id, allowed_workspace_ids=allowed)
        except RecordNotFoundError:
            return Response({"error": "Chunk not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


class KnowledgeDocumentDetailAPIView(AuthenticatedAPIView):
    def get(self, request, record_id: UUID):
        allowed = allowed_workspace_ids(request.user)
        try:
            data = get_document(record_id, allowed_workspace_ids=allowed)
        except RecordNotFoundError:
            return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
        except RecordAccessError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)
