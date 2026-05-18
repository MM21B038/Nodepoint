import logging
from uuid import UUID

from nodepoint.backend.kg_builder import extract_knowledge_graph, ingest_knowledge_graph_for_chunk
from nodepoint.enums import Status
from nodepoint.models import DocumentChunk
from nodepoint.mongo.manager import get_chunk_text
from nodepoint.services.chunking import rollup_document_status

logger = logging.getLogger(__name__)

_INCOMPLETE_CHUNK_STATUSES = (
    Status.PENDING,
    Status.QUEUED,
    Status.INPROGRESS,
)


def _enqueue_vectors_if_document_ready(document_id: UUID) -> None:
    if DocumentChunk.objects.filter(
        document_id=document_id,
        status__in=_INCOMPLETE_CHUNK_STATUSES,
    ).exists():
        return
    from nodepoint.services.vector import vector_preprocess

    vector_preprocess(document_id=document_id)
    logger.info("Enqueued vectors for document %s after all chunks completed", document_id)


def process_chunk(chunk_id: UUID) -> None:
    try:
        chunk = DocumentChunk.objects.select_related("document").get(id=chunk_id)
    except DocumentChunk.DoesNotExist:
        logger.error("process_chunk: chunk %s not found", chunk_id)
        return

    doc = chunk.document
    DocumentChunk.objects.filter(id=chunk_id).update(status=Status.INPROGRESS)
    logger.info("Processing chunk %s (doc %s index %s)", chunk_id, doc.id, chunk.index)

    try:
        text = get_chunk_text(chunk_id)
        if text is None or not text.strip():
            DocumentChunk.objects.filter(id=chunk_id).update(status=Status.FAILED)
            rollup_document_status(doc.id)
            logger.error("process_chunk: missing Mongo text for chunk %s", chunk_id)
            return

        entities, relations = extract_knowledge_graph(text)
        ok, _, _ = ingest_knowledge_graph_for_chunk(doc, chunk, entities, relations)
        if not ok:
            DocumentChunk.objects.filter(id=chunk_id).update(status=Status.FAILED)
            rollup_document_status(doc.id)
            logger.error("process_chunk: KG ingest failed for chunk %s", chunk_id)
            return

        DocumentChunk.objects.filter(id=chunk_id).update(status=Status.COMPLETED)
        rollup_document_status(doc.id)
        _enqueue_vectors_if_document_ready(doc.id)
        logger.info(
            "Completed chunk %s (%s entities, %s relations)",
            chunk_id,
            len(entities),
            len(relations),
        )

    except Exception:
        DocumentChunk.objects.filter(id=chunk_id).update(status=Status.FAILED)
        rollup_document_status(doc.id)
        logger.exception("process_chunk failed for chunk %s", chunk_id)
