import logging
import os
import django_rq
from rq import Retry

from nodepoint.models import Document
from nodepoint.enums import Status
from nodepoint.backend.content_extractor import read_document_content
from nodepoint.backend.kg_builder import split_doc, extract_knowledge_graph, ingest_knowledge_graph
from nodepoint.mongo.manager import ingest_document

logger = logging.getLogger(__name__)


def process_doc(doc_id, filepath):
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        logger.error("Document %s does not exist (path %s)", doc_id, filepath)
        return

    Document.objects.filter(id=doc_id).update(status=Status.INPROGRESS)
    logger.info("Processing document %s at %s", doc_id, filepath)

    try:
        if not os.path.exists(filepath):
            Document.objects.filter(id=doc_id).update(status=Status.INVALID)
            logger.error("File missing for document %s: %s", doc_id, filepath)
            return

        content = read_document_content(filepath)
        if content is None:
            Document.objects.filter(id=doc_id).update(status=Status.INVALID)
            logger.error("Failed to read content for document %s", doc_id)
            return

        if not content.strip():
            Document.objects.filter(id=doc_id).update(status=Status.INVALID)
            logger.error("Empty content for document %s", doc_id)
            return

        if not ingest_document(str(doc_id), content):
            Document.objects.filter(id=doc_id).update(content=False, status=Status.FAILED)
            logger.error("Mongo ingest failed for document %s", doc_id)
            return

        Document.objects.filter(id=doc_id).update(content=True)
        logger.info("Mongo ingest succeeded for document %s", doc_id)

        entities: list = []
        relations: list = []

        chunks = split_doc(content)
        
        for idx, chunk in enumerate(chunks):
            logger.info("Processing chunk %d / %d for document %s", idx, len(chunks), doc_id)
            try:
                chunk_entities, chunk_relations = extract_knowledge_graph(chunk)
                entities.extend(chunk_entities)
                relations.extend(chunk_relations)
            except Exception:
                Document.objects.filter(id=doc_id).update(status=Status.FAILED)
                logger.exception("Knowledge graph extraction failed %s / %s for document %s", idx, len(chunks), doc_id)
                return

        ok, entity_ids, relation_ids = ingest_knowledge_graph(doc, entities, relations)
        if not ok:
            Document.objects.filter(id=doc_id).update(status=Status.FAILED)
            logger.error("Knowledge graph ingest failed for document %s", doc_id)
            return

        Document.objects.filter(id=doc_id).update(status=Status.COMPLETED)
        logger.info(
            "Completed document %s (%s entities, %s relations; vectors via pipeline job 3)",
            doc_id,
            len(entity_ids),
            len(relation_ids),
        )

    except Exception:
        Document.objects.filter(id=doc_id).update(status=Status.FAILED)
        logger.exception("Error processing document %s", doc_id)


def doc_preprocess(document_ids=None, workspace_name=None):
    qs = Document.objects.filter(status__in=[Status.PENDING, Status.FAILED])

    if document_ids:
        qs = qs.filter(id__in=document_ids)
    if workspace_name:
        qs = qs.filter(workspace__name=workspace_name)

    docs = list(qs.select_related("workspace"))
    if not docs:
        logger.info("No documents to process.")
        return 0

    Document.objects.filter(id__in=[d.id for d in docs]).update(status=Status.QUEUED)
    queue = django_rq.get_queue("default")
    enqueued = 0

    for doc in docs:
        if not doc.file:
            Document.objects.filter(id=doc.id).update(status=Status.INVALID)
            logger.error("Document %s has no file", doc.id)
            continue

        filepath = doc.file.path
        if not os.path.exists(filepath):
            Document.objects.filter(id=doc.id).update(status=Status.INVALID)
            logger.error("File missing for document %s: %s", doc.id, filepath)
            continue

        queue.enqueue(
            process_doc,
            doc.id,
            filepath,
            job_timeout="30m",
            retry=Retry(max=3, interval=[10, 30, 60]),
        )
        enqueued += 1
        logger.info("Queued document %s (%s)", doc.id, filepath)

    return enqueued
