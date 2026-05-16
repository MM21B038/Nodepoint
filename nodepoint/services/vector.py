import logging

import django_rq
from rq import Retry

from nodepoint.models import KnowledgeEntity, KnowledgeRelation
from nodepoint.enums import Status
from nodepoint.backend.vector import ingest_entity_vector, ingest_relation_vector

logger = logging.getLogger(__name__)


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


def vector_preprocess(document_id=None):
    entity_qs = KnowledgeEntity.objects.filter(vector__in=[Status.PENDING, Status.FAILED])
    relation_qs = KnowledgeRelation.objects.filter(vector__in=[Status.PENDING, Status.FAILED])

    if document_id is not None:
        entity_qs = entity_qs.filter(document_id=document_id)
        relation_qs = relation_qs.filter(document_id=document_id)

    entity_ids = list(entity_qs.values_list("id", flat=True))
    relation_ids = list(relation_qs.values_list("id", flat=True))
    queue = django_rq.get_queue("default")

    for point_id in entity_ids:
        queue.enqueue(
            process_vector,
            point_id,
            "entity",
            job_timeout="30m",
            retry=Retry(max=3, interval=[10, 30, 60]),
        )

    for point_id in relation_ids:
        queue.enqueue(
            process_vector,
            point_id,
            "relation",
            job_timeout="30m",
            retry=Retry(max=3, interval=[10, 30, 60]),
        )

    logger.info(
        "Enqueued %s entities and %s relations for vector processing (document_id=%s)",
        len(entity_ids),
        len(relation_ids),
        document_id,
    )
