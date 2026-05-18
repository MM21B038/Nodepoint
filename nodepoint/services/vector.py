import logging

import django_rq
from rq import Retry

from nodepoint.models import DocumentChunk, KnowledgeEntity, KnowledgeRelation
from nodepoint.enums import Status
from nodepoint.backend.vector import (
    ingest_chunk_vector,
    ingest_entity_vector,
    ingest_relation_vector,
)
from nodepoint.services.chunking import CHUNK_JOB_TIMEOUT

logger = logging.getLogger(__name__)

_VECTOR_RETRY = Retry(max=3, interval=[10, 30, 60])


def process_vector(point_id, kind):
    if kind == "entity":
        entity = KnowledgeEntity.objects.select_related("document__workspace").get(id=point_id)
        if ingest_entity_vector(point_id, entity):
            KnowledgeEntity.objects.filter(id=point_id).update(vector=Status.COMPLETED)
        else:
            KnowledgeEntity.objects.filter(id=point_id).update(vector=Status.FAILED)
            logger.error("Failed to ingest vector for entity %s", point_id)
    elif kind == "relation":
        relation = KnowledgeRelation.objects.select_related(
            "source", "target", "document__workspace"
        ).get(id=point_id)
        if ingest_relation_vector(point_id, relation):
            KnowledgeRelation.objects.filter(id=point_id).update(vector=Status.COMPLETED)
        else:
            KnowledgeRelation.objects.filter(id=point_id).update(vector=Status.FAILED)
            logger.error("Failed to ingest vector for relation %s", point_id)
    elif kind == "chunk":
        chunk = DocumentChunk.objects.select_related("document__workspace").get(id=point_id)
        if ingest_chunk_vector(point_id, chunk):
            DocumentChunk.objects.filter(id=point_id).update(vector=Status.COMPLETED)
        else:
            DocumentChunk.objects.filter(id=point_id).update(vector=Status.FAILED)
            logger.error("Failed to ingest vector for chunk %s", point_id)


def vector_preprocess(document_id=None, workspace_name=None):
    entity_qs = KnowledgeEntity.objects.filter(vector__in=[Status.PENDING, Status.FAILED])
    relation_qs = KnowledgeRelation.objects.filter(vector__in=[Status.PENDING, Status.FAILED])
    chunk_qs = DocumentChunk.objects.filter(vector__in=[Status.PENDING, Status.FAILED])

    if document_id is not None:
        entity_qs = entity_qs.filter(document_id=document_id)
        relation_qs = relation_qs.filter(document_id=document_id)
        chunk_qs = chunk_qs.filter(document_id=document_id)
    elif workspace_name:
        entity_qs = entity_qs.filter(document__workspace__name=workspace_name)
        relation_qs = relation_qs.filter(document__workspace__name=workspace_name)
        chunk_qs = chunk_qs.filter(document__workspace__name=workspace_name)

    entity_ids = list(entity_qs.values_list("id", flat=True))
    relation_ids = list(relation_qs.values_list("id", flat=True))
    chunk_ids = list(chunk_qs.values_list("id", flat=True))
    queue = django_rq.get_queue("default")

    for point_id in entity_ids:
        queue.enqueue(
            process_vector,
            point_id,
            "entity",
            job_timeout=CHUNK_JOB_TIMEOUT,
            retry=_VECTOR_RETRY,
        )

    for point_id in relation_ids:
        queue.enqueue(
            process_vector,
            point_id,
            "relation",
            job_timeout=CHUNK_JOB_TIMEOUT,
            retry=_VECTOR_RETRY,
        )

    for point_id in chunk_ids:
        queue.enqueue(
            process_vector,
            point_id,
            "chunk",
            job_timeout=CHUNK_JOB_TIMEOUT,
            retry=_VECTOR_RETRY,
        )

    logger.info(
        "Enqueued %s entities, %s relations, %s chunks for vector processing "
        "(document_id=%s, workspace=%s)",
        len(entity_ids),
        len(relation_ids),
        len(chunk_ids),
        document_id,
        workspace_name,
    )
