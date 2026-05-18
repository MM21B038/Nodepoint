from __future__ import annotations

import logging
import os
from uuid import UUID

import django_rq
from django.db.models import Count, Q
from rq import Retry

from nodepoint.backend.content_extractor import read_document_content
from nodepoint.backend.kg_builder import split_doc
from nodepoint.enums import Status
from nodepoint.models import Document, DocumentChunk, KnowledgeEntity
from nodepoint.mongo.manager import delete_chunks_for_document, ingest_chunk

logger = logging.getLogger(__name__)

CHUNK_JOB_TIMEOUT = "5m"
CHUNK_JOB_WAIT_SECONDS = 5 * 60
_CHUNK_RETRY = Retry(max=3, interval=[10, 30, 60])

_INCOMPLETE_CHUNK_STATUSES = (
    Status.PENDING,
    Status.FAILED,
    Status.QUEUED,
)

_INCOMPLETE_DOC_STATUSES = (
    Status.PENDING,
    Status.FAILED,
    Status.QUEUED,
)


def rollup_document_status(document_id: UUID) -> None:
    chunks = list(DocumentChunk.objects.filter(document_id=document_id).only("status"))
    if not chunks:
        if KnowledgeEntity.objects.filter(document_id=document_id).exists():
            return
        Document.objects.filter(id=document_id).update(status=Status.QUEUED)
        return

    statuses = {c.status for c in chunks}
    if Status.FAILED in statuses:
        doc_status = Status.FAILED
    elif all(c.status == Status.COMPLETED for c in chunks):
        doc_status = Status.COMPLETED
    elif Status.INPROGRESS in statuses:
        doc_status = Status.INPROGRESS
    elif Status.QUEUED in statuses:
        doc_status = Status.QUEUED
    else:
        doc_status = Status.INPROGRESS

    Document.objects.filter(id=document_id).update(status=doc_status)


def prepare_document(doc_id: UUID, filepath: str) -> list[UUID]:
    """Split file into chunks in Postgres + Mongo; return chunk ids."""
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        logger.error("prepare_document: document %s not found", doc_id)
        return []

    Document.objects.filter(id=doc_id).update(status=Status.INPROGRESS)

    if not os.path.exists(filepath):
        Document.objects.filter(id=doc_id).update(status=Status.INVALID, content=False)
        logger.error("prepare_document: file missing for %s: %s", doc_id, filepath)
        return []

    content = read_document_content(filepath)
    if content is None:
        Document.objects.filter(id=doc_id).update(status=Status.INVALID, content=False)
        logger.error("prepare_document: failed to read %s", doc_id)
        return []

    if not content.strip():
        Document.objects.filter(id=doc_id).update(status=Status.INVALID, content=False)
        logger.error("prepare_document: empty content for %s", doc_id)
        return []

    text_chunks = split_doc(content)
    if not text_chunks:
        Document.objects.filter(id=doc_id).update(status=Status.INVALID, content=False)
        return []

    delete_chunks_for_document(doc_id)
    DocumentChunk.objects.filter(document_id=doc_id).delete()

    chunk_ids: list[UUID] = []
    for index, text in enumerate(text_chunks):
        chunk = DocumentChunk.objects.create(
            document=doc,
            index=index,
            status=Status.PENDING,
            vector=Status.PENDING,
        )
        if not ingest_chunk(chunk.id, doc.id, index, text):
            chunk.delete()
            Document.objects.filter(id=doc_id).update(status=Status.FAILED, content=False)
            logger.error("prepare_document: Mongo ingest failed for chunk %s", chunk.id)
            return chunk_ids
        chunk_ids.append(chunk.id)

    Document.objects.filter(id=doc_id).update(content=True, status=Status.QUEUED)
    logger.info(
        "prepare_document: created %s chunks for document %s",
        len(chunk_ids),
        doc_id,
    )
    return chunk_ids


def documents_needing_prepare_qs(workspace_name: str | None = None):
    qs = Document.objects.exclude(status__in=[Status.INVALID, Status.TERMINATED])
    if workspace_name:
        qs = qs.filter(workspace__name=workspace_name)
    return qs.annotate(chunk_count=Count("chunks")).filter(
        Q(chunk_count=0) | Q(content=False)
    )


def run_prepare_legacy_batch(workspace_name: str | None = None) -> int:
    """Create DocumentChunk + Mongo rows for documents not yet migrated."""
    prepared = 0
    for doc in documents_needing_prepare_qs(workspace_name):
        if not doc.file:
            Document.objects.filter(id=doc.id).update(status=Status.INVALID)
            continue
        filepath = doc.file.path
        if not os.path.exists(filepath):
            Document.objects.filter(id=doc.id).update(status=Status.INVALID)
            continue
        chunk_ids = prepare_document(doc.id, filepath)
        if chunk_ids:
            prepared += 1
            logger.info(
                "run_prepare_legacy_batch: prepared %s chunks for document %s",
                len(chunk_ids),
                doc.id,
            )
    logger.info("run_prepare_legacy_batch: migrated %s document(s)", prepared)
    return prepared


def wait_for_chunk_jobs(jobs: list) -> None:
    """Block until each enqueued process_chunk RQ job finishes (or times out)."""
    for job in jobs:
        try:
            job.wait(timeout=CHUNK_JOB_WAIT_SECONDS)
        except Exception:
            logger.exception("chunk job %s did not finish within %ss", job.id, CHUNK_JOB_WAIT_SECONDS)


def enqueue_chunks_for_documents(
    document_ids: list[UUID] | None = None,
    workspace_name: str | None = None,
    *,
    wait: bool = False,
) -> int:
    from nodepoint.services.chunk_process import process_chunk

    doc_qs = Document.objects.filter(status__in=_INCOMPLETE_DOC_STATUSES)
    if document_ids:
        doc_qs = doc_qs.filter(id__in=document_ids)
    if workspace_name:
        doc_qs = doc_qs.filter(workspace__name=workspace_name)

    chunk_qs = DocumentChunk.objects.filter(
        document_id__in=doc_qs.values("id"),
        status__in=_INCOMPLETE_CHUNK_STATUSES,
    ).select_related("document")

    chunks = list(chunk_qs)
    if not chunks:
        logger.info("enqueue_chunks: no chunks to queue")
        return 0

    queue = django_rq.get_queue("default")
    jobs = []
    for chunk in chunks:
        DocumentChunk.objects.filter(id=chunk.id).update(status=Status.QUEUED)
        job = queue.enqueue(
            process_chunk,
            chunk.id,
            job_timeout=CHUNK_JOB_TIMEOUT,
            retry=_CHUNK_RETRY,
        )
        jobs.append(job)
        logger.info("Queued chunk %s (doc %s index %s)", chunk.id, chunk.document_id, chunk.index)

    if wait and jobs:
        wait_for_chunk_jobs(jobs)

    return len(jobs)


def enqueue_chunks_for_document(
    document_id: UUID,
    workspace_name: str | None = None,
) -> int:
    return enqueue_chunks_for_documents(
        document_ids=[document_id],
        workspace_name=workspace_name,
    )
