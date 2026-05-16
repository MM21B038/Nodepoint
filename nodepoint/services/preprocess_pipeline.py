from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

import django_rq
from rq import Retry

from nodepoint.backend.content_extractor import read_document_content
from nodepoint.enums import Status
from nodepoint.models import Document
from nodepoint.mongo.manager import ingest_document
from nodepoint.services.document import doc_preprocess, process_doc
from nodepoint.services.vector import vector_preprocess

logger = logging.getLogger(__name__)

_JOB_TIMEOUT = "30m"
_RETRY = Retry(max=3, interval=[10, 30, 60])


def run_process_document(doc_id: UUID) -> None:
    """Job 1: full Mongo + KG for a single uploaded document."""
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        logger.error("run_process_document: document %s not found", doc_id)
        return

    if not doc.file:
        Document.objects.filter(id=doc_id).update(status=Status.INVALID)
        logger.error("run_process_document: document %s has no file", doc_id)
        return

    filepath = doc.file.path
    if not os.path.exists(filepath):
        Document.objects.filter(id=doc_id).update(status=Status.INVALID)
        logger.error("run_process_document: file missing for %s: %s", doc_id, filepath)
        return

    process_doc(doc.id, filepath)


def run_doc_preprocess_batch() -> int:
    """Job 2: enqueue process_doc for all PENDING/FAILED documents (global)."""
    count = doc_preprocess()
    logger.info("run_doc_preprocess_batch: queued %s document(s)", count)
    return count


def run_vector_preprocess_batch() -> None:
    """Job 3: enqueue embeddings for all PENDING/FAILED entity/relation vectors (global)."""
    vector_preprocess()
    logger.info("run_vector_preprocess_batch: global vector sweep enqueued")


def run_mongo_content_repair_batch() -> int:
    """Job 4: re-ingest Mongo for all documents with content=false (no KG/vectors)."""
    docs = list(Document.objects.filter(content=False).select_related("workspace"))
    repaired = 0

    for doc in docs:
        if not doc.file:
            logger.error("mongo repair: document %s has no file", doc.id)
            continue

        filepath = doc.file.path
        if not os.path.exists(filepath):
            logger.error("mongo repair: file missing for %s: %s", doc.id, filepath)
            continue

        content = read_document_content(filepath)
        if content is None:
            Document.objects.filter(id=doc.id).update(content=False, status=Status.FAILED)
            logger.error("mongo repair: failed to read content for %s", doc.id)
            continue

        if not content.strip():
            Document.objects.filter(id=doc.id).update(content=False, status=Status.INVALID)
            logger.error("mongo repair: empty content for %s", doc.id)
            continue

        if not ingest_document(str(doc.id), content):
            Document.objects.filter(id=doc.id).update(content=False, status=Status.FAILED)
            logger.error("mongo repair: Mongo ingest failed for %s", doc.id)
            continue

        Document.objects.filter(id=doc.id).update(content=True)
        repaired += 1
        logger.info("mongo repair: content restored for document %s", doc.id)

    logger.info("run_mongo_content_repair_batch: repaired %s document(s)", repaired)
    return repaired


def enqueue_preprocess_pipeline(uploaded_document_id: UUID | None = None) -> dict[str, Any]:
    """
    Orchestrate preprocess pipeline:
    - Upload: job1 (uploaded doc) -> job2 || job3 -> job4
    - API only: job2 || job3 -> job4
    """
    queue = django_rq.get_queue("default")
    job_kwargs = {"job_timeout": _JOB_TIMEOUT, "retry": _RETRY}

    steps: list[str] = []
    jobs: dict[str, str | None] = {}

    if uploaded_document_id is not None:
        j1 = queue.enqueue(
            run_process_document,
            uploaded_document_id,
            **job_kwargs,
        )
        jobs["uploaded_document"] = j1.id
        steps.append("uploaded_document")

        j2 = queue.enqueue(
            run_doc_preprocess_batch,
            depends_on=j1,
            **job_kwargs,
        )
        j3 = queue.enqueue(
            run_vector_preprocess_batch,
            depends_on=j1,
            **job_kwargs,
        )
        jobs["doc_preprocess_global"] = j2.id
        jobs["vector_preprocess_global"] = j3.id
        steps.extend(["doc_preprocess_global", "vector_preprocess_global"])

        j4 = queue.enqueue(
            run_mongo_content_repair_batch,
            depends_on=[j2, j3],
            **job_kwargs,
        )
        jobs["mongo_content_repair"] = j4.id
        steps.append("mongo_content_repair")

        message = "Preprocess pipeline queued (4 steps)"
    else:
        j2 = queue.enqueue(run_doc_preprocess_batch, **job_kwargs)
        j3 = queue.enqueue(run_vector_preprocess_batch, **job_kwargs)
        jobs["doc_preprocess_global"] = j2.id
        jobs["vector_preprocess_global"] = j3.id
        steps.extend(["doc_preprocess_global", "vector_preprocess_global"])

        j4 = queue.enqueue(
            run_mongo_content_repair_batch,
            depends_on=[j2, j3],
            **job_kwargs,
        )
        jobs["mongo_content_repair"] = j4.id
        steps.append("mongo_content_repair")

        message = "Preprocess pipeline queued (3 steps)"

    logger.info("%s: %s", message, jobs)
    return {"message": message, "steps": steps, "jobs": jobs}
