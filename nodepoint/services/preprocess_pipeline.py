from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

import django_rq
from rq import Retry

from nodepoint.backend.content_extractor import read_document_content
from nodepoint.backend.kg_builder import split_doc
from nodepoint.enums import Status
from nodepoint.models import Document, DocumentChunk
from nodepoint.mongo.manager import delete_chunks_for_document, ingest_chunk
from nodepoint.services.chunking import (
    enqueue_chunks_for_documents,
    prepare_document,
    run_prepare_legacy_batch,
)
from nodepoint.services.vector import vector_preprocess

logger = logging.getLogger(__name__)

_ORCHESTRATOR_TIMEOUT = "30m"
_RETRY = Retry(max=3, interval=[10, 30, 60])


def run_prepare_document(doc_id: UUID) -> None:
    """Job 1: split file into chunks (Postgres + Mongo) for uploaded document."""
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        logger.error("run_prepare_document: document %s not found", doc_id)
        return

    if not doc.file:
        Document.objects.filter(id=doc_id).update(status=Status.INVALID)
        return

    filepath = doc.file.path
    if not os.path.exists(filepath):
        Document.objects.filter(id=doc_id).update(status=Status.INVALID)
        return

    prepare_document(doc_id, filepath)


def run_chunk_preprocess_batch(workspace_name: str | None = None) -> int:
    """Enqueue process_chunk jobs and wait for them before the vector step runs."""
    count = enqueue_chunks_for_documents(workspace_name=workspace_name, wait=True)
    logger.info(
        "run_chunk_preprocess_batch: finished %s chunk job(s) (workspace=%s)",
        count,
        workspace_name,
    )
    return count


def run_vector_preprocess_batch(workspace_name: str | None = None) -> None:
    """Enqueue embeddings for entities, relations, and chunks."""
    vector_preprocess(workspace_name=workspace_name)
    logger.info(
        "run_vector_preprocess_batch: vector sweep enqueued (workspace=%s)",
        workspace_name,
    )


def run_chunk_mongo_repair_batch(workspace_name: str | None = None) -> int:
    """Rebuild Mongo chunk rows for documents with content=false or missing chunk text."""
    repaired = 0
    doc_qs = Document.objects.filter(content=False)
    if workspace_name:
        doc_qs = doc_qs.filter(workspace__name=workspace_name)
    doc_ids = set(doc_qs.values_list("id", flat=True))

    content_qs = Document.objects.filter(content=True)
    if workspace_name:
        content_qs = content_qs.filter(workspace__name=workspace_name)

    for doc in content_qs.prefetch_related("chunks"):
        for chunk in doc.chunks.all():
            from nodepoint.mongo.manager import get_chunk_text

            if get_chunk_text(chunk.id) is None:
                doc_ids.add(doc.id)
                break

    for doc in Document.objects.filter(id__in=doc_ids).select_related("workspace"):
        if not doc.file or not os.path.exists(doc.file.path):
            continue

        content = read_document_content(doc.file.path)
        if content is None or not content.strip():
            Document.objects.filter(id=doc.id).update(content=False, status=Status.FAILED)
            continue

        text_chunks = split_doc(content)
        if not text_chunks:
            continue

        delete_chunks_for_document(doc.id)
        DocumentChunk.objects.filter(document_id=doc.id).delete()

        ok = True
        for index, text in enumerate(text_chunks):
            chunk = DocumentChunk.objects.create(
                document=doc,
                index=index,
                status=Status.PENDING,
                vector=Status.PENDING,
            )
            if not ingest_chunk(chunk.id, doc.id, index, text):
                ok = False
                chunk.delete()
                break

        if ok:
            Document.objects.filter(id=doc.id).update(content=True, status=Status.QUEUED)
            repaired += 1
        else:
            Document.objects.filter(id=doc.id).update(content=False, status=Status.FAILED)

    logger.info(
        "run_chunk_mongo_repair_batch: repaired %s document(s) (workspace=%s)",
        repaired,
        workspace_name,
    )
    return repaired


def enqueue_preprocess_pipeline(
    uploaded_document_id: UUID | None = None,
    workspace_name: str | None = None,
) -> dict[str, Any]:
    queue = django_rq.get_queue("default")
    job_kwargs = {"job_timeout": _ORCHESTRATOR_TIMEOUT, "retry": _RETRY}

    steps: list[str] = []
    jobs: dict[str, str | None] = {}
    prepare_deps: list = []

    if uploaded_document_id is not None:
        j1 = queue.enqueue(run_prepare_document, uploaded_document_id, **job_kwargs)
        jobs["prepare_document"] = j1.id
        steps.append("prepare_document")
        prepare_deps.append(j1)
    else:
        j_legacy = queue.enqueue(
            run_prepare_legacy_batch,
            workspace_name,
            **job_kwargs,
        )
        jobs["prepare_legacy"] = j_legacy.id
        steps.append("prepare_legacy")
        prepare_deps.append(j_legacy)

    j2 = queue.enqueue(
        run_chunk_preprocess_batch,
        workspace_name,
        depends_on=prepare_deps,
        **job_kwargs,
    )
    jobs["chunk_preprocess"] = j2.id
    steps.append("chunk_preprocess")

    j3 = queue.enqueue(
        run_vector_preprocess_batch,
        workspace_name,
        depends_on=j2,
        **job_kwargs,
    )
    jobs["vector_preprocess"] = j3.id
    steps.append("vector_preprocess")

    j4 = queue.enqueue(
        run_chunk_mongo_repair_batch,
        workspace_name,
        depends_on=j3,
        **job_kwargs,
    )
    jobs["chunk_mongo_repair"] = j4.id
    steps.append("chunk_mongo_repair")

    if uploaded_document_id is not None:
        message = (
            "Preprocess pipeline queued: prepare document → chunk KG (parallel) "
            "→ embeddings → mongo repair"
        )
    else:
        message = (
            "Preprocess pipeline queued: prepare legacy → chunk KG (parallel) "
            "→ embeddings → mongo repair"
        )
        if workspace_name:
            message += f" (workspace={workspace_name})"

    logger.info("%s: %s", message, jobs)
    return {"message": message, "steps": steps, "jobs": jobs}
