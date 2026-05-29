import logging
import os
from uuid import UUID

from nodepoint.enums import Status
from nodepoint.models import Document
from nodepoint.services.chunking import (
    enqueue_chunks_for_document,
    enqueue_chunks_for_documents,
    prepare_document,
)

logger = logging.getLogger(__name__)


def process_doc(doc_id, filepath):
    """Prepare chunks and enqueue per-chunk KG jobs (legacy entry point name)."""
    chunk_ids = prepare_document(doc_id, filepath)
    if not chunk_ids:
        return
    enqueue_chunks_for_document(doc_id)


def doc_preprocess(document_ids=None, workspace_name=None):
    """Enqueue chunk jobs for documents with incomplete status (global or filtered)."""
    from django.db.models import Count, Q

    from nodepoint.models import Document

    qs = Document.objects.annotate(chunk_count=Count("chunks")).filter(
        Q(status__in=[Status.PENDING, Status.FAILED, Status.QUEUED])
        | Q(status=Status.COMPLETED, chunk_count=0)
    )
    if document_ids:
        qs = qs.filter(id__in=document_ids)
    if workspace_name:
        qs = qs.filter(workspace__name=workspace_name)

    docs = list(qs)
    if not docs:
        logger.info("No documents to process.")
        return 0

    ids = [d.id for d in docs]
    for doc in docs:
        if not doc.file:
            Document.objects.filter(id=doc.id).update(status=Status.INVALID)
            logger.error("Document %s has no file", doc.id)
            continue
        if not os.path.exists(doc.file.path):
            Document.objects.filter(id=doc.id).update(status=Status.INVALID)
            logger.error("File missing for document %s", doc.id)
            continue
        if getattr(doc, "chunk_count", 0) == 0:
            prepare_document(doc.id, doc.file.path)

    return enqueue_chunks_for_documents(document_ids=ids, workspace_name=workspace_name)
